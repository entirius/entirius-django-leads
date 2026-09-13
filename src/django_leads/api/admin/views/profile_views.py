# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — analysis and recipient-pick prompt profiles (CRUD); lists never carry the prompt."""

from django.db.models import Model
from drf_spectacular.utils import extend_schema, extend_schema_view
from pydantic import BaseModel
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, AdminView, Conflict, parse
from django_leads.models import AnalysisProfile, RecipientPickProfile
from django_leads.schemas import requests, responses
from django_leads.services import config_service


class _ProfileView(AdminView):
    model: type[Model]
    create_schema: type[BaseModel]
    update_schema: type[BaseModel]
    list_item: type[BaseModel]
    detail_item: type[BaseModel]

    def rows(self, channel_idx: str):
        return self.model.objects.filter(channel=self.channel(channel_idx)).order_by("key")

    def dump(self, profile: Model, schema: type[BaseModel]) -> dict:
        return schema.model_validate(profile).model_dump(mode="json")

    def save(self, profile: Model, fields: dict) -> dict:
        try:
            profile = config_service.save_profile(profile, fields)
        except config_service.ProfileExists as error:
            raise Conflict(str(error)) from None
        return self.dump(profile, self.detail_item)


class _ProfileListView(_ProfileView):
    def get(self, request: Request, channel_idx: str) -> Response:
        return Response({"results": [self.dump(row, self.list_item) for row in self.rows(channel_idx)]})

    def post(self, request: Request, channel_idx: str) -> Response:
        fields = parse(self.create_schema, request.data).model_dump(mode="json")
        return Response(self.save(self.model(channel=self.channel(channel_idx)), fields), status=201)


class _ProfileDetailView(_ProfileView):
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        return Response(self.dump(self.get_in(self.rows(channel_idx), pk, "Profile"), self.detail_item))

    def patch(self, request: Request, channel_idx: str, pk: int) -> Response:
        updates = parse(self.update_schema, request.data).model_dump(mode="json", exclude_unset=True)
        if None in updates.values():
            raise ValidationError({"detail": ["fields may not be null"]})
        return Response(self.save(self.get_in(self.rows(channel_idx), pk, "Profile"), updates))

    def delete(self, request: Request, channel_idx: str, pk: int) -> Response:
        self.get_in(self.rows(channel_idx), pk, "Profile").delete()
        return Response(status=204)


def _list_schema(tag: str, name: str, create, detail, listing):
    return extend_schema_view(
        get=extend_schema(tags=[tag], operation_id=f"leads_{name}_list", summary="Profiles of the channel", responses={200: listing, **ERROR_RESPONSES}),
        post=extend_schema(tags=[tag], summary="Add a profile", request=create, responses={201: detail, **ERROR_RESPONSES, 409: None}),
    )  # fmt: skip


def _detail_schema(tag: str, update, detail):
    return extend_schema_view(
        get=extend_schema(tags=[tag], summary="One profile with its prompt", responses={200: detail, **ERROR_RESPONSES}),
        patch=extend_schema(tags=[tag], summary="Update a profile", request=update, responses={200: detail, **ERROR_RESPONSES, 409: None}),
        delete=extend_schema(tags=[tag], summary="Delete a profile", responses={204: None, **ERROR_RESPONSES}),
    )  # fmt: skip


class _Analysis:
    model = AnalysisProfile
    create_schema = requests.AnalysisProfileRequest
    update_schema = requests.AnalysisProfileUpdateRequest
    list_item = responses.AnalysisProfileResponse
    detail_item = responses.AnalysisProfileDetailResponse


class _Recipient:
    model = RecipientPickProfile
    create_schema = requests.ProfileRequest
    update_schema = requests.ProfileUpdateRequest
    list_item = responses.ProfileResponse
    detail_item = responses.ProfileDetailResponse


_ANALYSIS_TAG, _RECIPIENT_TAG = "Leads analysis profiles", "Leads recipient profiles"


@_list_schema(
    _ANALYSIS_TAG,
    "analysis_profiles",
    _Analysis.create_schema,
    _Analysis.detail_item,
    responses.AnalysisProfileListResponse,
)
class AnalysisProfileListView(_Analysis, _ProfileListView):
    pass


@_detail_schema(_ANALYSIS_TAG, _Analysis.update_schema, _Analysis.detail_item)
class AnalysisProfileDetailView(_Analysis, _ProfileDetailView):
    pass


@_list_schema(
    _RECIPIENT_TAG,
    "recipient_profiles",
    _Recipient.create_schema,
    _Recipient.detail_item,
    responses.ProfileListResponse,
)
class RecipientProfileListView(_Recipient, _ProfileListView):
    pass


@_detail_schema(_RECIPIENT_TAG, _Recipient.update_schema, _Recipient.detail_item)
class RecipientProfileDetailView(_Recipient, _ProfileDetailView):
    pass
