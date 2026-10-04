# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — lead types: CRUD; a type still carried by companies cannot be deleted (409)."""

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, AdminView, Conflict, parse
from django_leads.models import LeadType
from django_leads.schemas.requests import LeadTypeRequest, LeadTypeUpdateRequest
from django_leads.schemas.responses import LeadTypeListResponse, LeadTypeResponse
from django_leads.services import lead_type_service

_TAGS = ["Leads lead types"]


def dump(lead_type: LeadType) -> dict:
    return LeadTypeResponse.model_validate(lead_type).model_dump(mode="json")


class LeadTypeListView(AdminView):
    access_area = "leads.settings"

    @extend_schema(
        tags=_TAGS,
        operation_id="leads_lead_types_list",
        summary="Lead types of the channel in display order",
        description="Inactive types are listed too; UNKNOWN is built in and never listed.",
        responses={200: LeadTypeListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        lead_types = lead_type_service.list_lead_types(self.channel(channel_idx))
        return Response({"results": [dump(lead_type) for lead_type in lead_types]})

    @extend_schema(
        tags=_TAGS,
        summary="Add a lead type",
        request=LeadTypeRequest,
        responses={201: LeadTypeResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        fields = parse(LeadTypeRequest, request.data).model_dump(mode="json")
        try:
            lead_type = lead_type_service.create_lead_type(self.channel(channel_idx), fields)
        except lead_type_service.LeadTypeExists as error:
            raise Conflict(str(error), code="lead_type_exists") from None
        return Response(dump(lead_type), status=201)


class LeadTypeDetailView(AdminView):
    access_area = "leads.settings"

    def lead_type(self, channel_idx: str, pk: int) -> LeadType:
        return self.get_in(lead_type_service.list_lead_types(self.channel(channel_idx)), pk, "Lead type")

    @extend_schema(tags=_TAGS, summary="One lead type", responses={200: LeadTypeResponse, **ERROR_RESPONSES})
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        return Response(dump(self.lead_type(channel_idx, pk)))

    @extend_schema(
        tags=_TAGS,
        summary="Update a lead type",
        description="Label, order and is_active; the code is fixed after create.",
        request=LeadTypeUpdateRequest,
        responses={200: LeadTypeResponse, **ERROR_RESPONSES},
    )
    def patch(self, request: Request, channel_idx: str, pk: int) -> Response:
        updates = parse(LeadTypeUpdateRequest, request.data).model_dump(mode="json", exclude_unset=True)
        if None in updates.values():
            raise ValidationError({"detail": ["fields may not be null"]})
        return Response(dump(lead_type_service.update_lead_type(self.lead_type(channel_idx, pk), updates)))

    @extend_schema(
        tags=_TAGS,
        summary="Delete a lead type",
        description="409 while companies carry the code — retype them, or deactivate the type instead.",
        responses={204: None, **ERROR_RESPONSES, 409: None},
    )
    def delete(self, request: Request, channel_idx: str, pk: int) -> Response:
        try:
            lead_type_service.delete_lead_type(self.lead_type(channel_idx, pk))
        except lead_type_service.LeadTypeInUse as error:
            raise Conflict(str(error), code="lead_type_in_use") from None
        return Response(status=204)
