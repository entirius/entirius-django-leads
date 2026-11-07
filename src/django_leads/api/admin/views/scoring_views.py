# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — scoring profiles (CRUD) and a manual score run on one company."""

from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, AdminView, Conflict, parse
from django_leads.models import Company, ScoringProfile
from django_leads.schemas.requests import ScoringProfileRequest, ScoringProfileUpdateRequest
from django_leads.schemas.responses import ScoreResponse, ScoringProfileListResponse, ScoringProfileResponse
from django_leads.services import config_service, scoring_service

_TAGS = ["Leads Scoring"]


class _ScoringView(AdminView):
    access_area = "leads.settings"

    def rows(self, channel_idx: str):
        return ScoringProfile.objects.filter(channel=self.channel(channel_idx)).order_by("key")

    def save(self, profile: ScoringProfile, fields: dict) -> dict:
        try:
            profile = config_service.save_profile(profile, fields)
        except config_service.ProfileExists as error:
            raise Conflict(str(error), code="profile_exists") from None
        return ScoringProfileResponse.model_validate(profile).model_dump(mode="json")


@extend_schema_view(
    get=extend_schema(
        tags=_TAGS,
        operation_id="leads_scoring_profiles_list",
        summary="Scoring profiles of the channel",
        responses={200: ScoringProfileListResponse, **ERROR_RESPONSES},
    ),
    post=extend_schema(
        tags=_TAGS,
        summary="Add a scoring profile",
        request=ScoringProfileRequest,
        responses={201: ScoringProfileResponse, **ERROR_RESPONSES, 409: None},
    ),
)
class ScoringProfileListView(_ScoringView):
    def get(self, request: Request, channel_idx: str) -> Response:
        rows = [ScoringProfileResponse.model_validate(row).model_dump(mode="json") for row in self.rows(channel_idx)]
        return Response({"results": rows})

    def post(self, request: Request, channel_idx: str) -> Response:
        fields = parse(ScoringProfileRequest, request.data).model_dump(mode="json")
        return Response(self.save(ScoringProfile(channel=self.channel(channel_idx)), fields), status=201)


@extend_schema_view(
    get=extend_schema(
        tags=_TAGS, summary="One scoring profile", responses={200: ScoringProfileResponse, **ERROR_RESPONSES}
    ),
    patch=extend_schema(
        tags=_TAGS,
        summary="Update a scoring profile",
        request=ScoringProfileUpdateRequest,
        responses={200: ScoringProfileResponse, **ERROR_RESPONSES, 409: None},
    ),
    delete=extend_schema(tags=_TAGS, summary="Delete a scoring profile", responses={204: None, **ERROR_RESPONSES}),
)
class ScoringProfileDetailView(_ScoringView):
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        profile = self.get_in(self.rows(channel_idx), pk, "Profile")
        return Response(ScoringProfileResponse.model_validate(profile).model_dump(mode="json"))

    def patch(self, request: Request, channel_idx: str, pk: int) -> Response:
        updates = parse(ScoringProfileUpdateRequest, request.data).model_dump(mode="json", exclude_unset=True)
        if None in updates.values():
            raise ValidationError({"detail": ["fields may not be null"]})
        return Response(self.save(self.get_in(self.rows(channel_idx), pk, "Profile"), updates))

    def delete(self, request: Request, channel_idx: str, pk: int) -> Response:
        self.get_in(self.rows(channel_idx), pk, "Profile").delete()
        return Response(status=204)


class CompanyScoreView(AdminView):
    access_area = "leads.companies"

    @extend_schema(
        tags=["Leads Companies"],
        summary="Score the company now",
        description="Runs the channel's active scoring profile and writes Activity `score`. 409 "
        "`NO_SCORING_PROFILE` when the channel has none.",
        request=None,
        responses={200: ScoreResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        companies = Company.objects.filter(channel=self.channel(channel_idx))
        result = scoring_service.score_and_record(self.get_in(companies, pk, "Company"), actor=request.user.username)
        if result is None:
            raise Conflict("no active scoring profile", code="no_scoring_profile")
        return Response(ScoreResponse(**result.as_data()).model_dump())
