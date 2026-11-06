# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — pilots (CRUD) and the pilot report."""

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, PAGE_PARAMETERS, AdminView, parse
from django_leads.models import Company, Pilot
from django_leads.schemas.requests import PilotListQuery, PilotReportQuery, PilotRequest, PilotUpdateRequest
from django_leads.schemas.responses import PilotListResponse, PilotReportResponse, PilotResponse
from django_leads.services import pilot_service

_TAGS = ["Leads Pilots"]
_NULLABLE = {"end_date", "license_switch_date"}


def dump(pilot: Pilot) -> PilotResponse:
    return PilotResponse.model_validate(pilot)


class _PilotView(AdminView):
    access_area = "leads.companies"

    def pilot(self, channel_idx: str, pk: int) -> Pilot:
        pilots = pilot_service.list_pilots(self.channel(channel_idx))
        return self.get_in(pilots, pk, "Pilot")


class PilotListView(_PilotView):
    @extend_schema(
        tags=_TAGS,
        operation_id="leads_pilots_list",
        summary="Pilots of the channel, newest start first",
        parameters=[
            OpenApiParameter("company", int, description="Company id."),
            OpenApiParameter("result", str, description="pending, success, failure or cancelled."),
            *PAGE_PARAMETERS,
        ],
        responses={200: PilotListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(PilotListQuery, request.query_params.dict())
        pilots = pilot_service.list_pilots(
            self.channel(channel_idx), company_id=query.company, result=query.result or ""
        )
        return self.paginated(request, pilots, dump)

    @extend_schema(
        tags=_TAGS,
        summary="Start a pilot for a company",
        request=PilotRequest,
        responses={201: PilotResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        fields = parse(PilotRequest, request.data).model_dump()
        company = self.get_in(
            Company.objects.filter(channel=self.channel(channel_idx)), fields.pop("company_id"), "Company"
        )
        try:
            pilot = pilot_service.create_pilot(company, fields, actor=request.user.username)
        except ValueError as error:
            raise ValidationError({"detail": [str(error)]}) from None
        return Response(dump(pilot).model_dump(mode="json"), status=201)


class PilotDetailView(_PilotView):
    @extend_schema(tags=_TAGS, summary="One pilot", responses={200: PilotResponse, **ERROR_RESPONSES})
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        return Response(dump(self.pilot(channel_idx, pk)).model_dump(mode="json"))

    @extend_schema(
        tags=_TAGS,
        summary="Update a pilot",
        description="A change of `result` writes a timeline note. Null only clears `end_date` and "
        "`license_switch_date`.",
        request=PilotUpdateRequest,
        responses={200: PilotResponse, **ERROR_RESPONSES},
    )
    def patch(self, request: Request, channel_idx: str, pk: int) -> Response:
        updates = parse(PilotUpdateRequest, request.data).model_dump(exclude_unset=True)
        if any(value is None for field, value in updates.items() if field not in _NULLABLE):
            raise ValidationError({"detail": ["null is only allowed for end_date and license_switch_date"]})
        try:
            pilot = pilot_service.update_pilot(self.pilot(channel_idx, pk), updates, actor=request.user.username)
        except ValueError as error:
            raise ValidationError({"detail": [str(error)]}) from None
        return Response(dump(pilot).model_dump(mode="json"))

    @extend_schema(tags=_TAGS, summary="Delete a pilot", responses={204: None, **ERROR_RESPONSES})
    def delete(self, request: Request, channel_idx: str, pk: int) -> Response:
        self.pilot(channel_idx, pk).delete()
        return Response(status=204)


class PilotReportView(AdminView):
    access_area = "leads.companies"

    @extend_schema(
        tags=_TAGS,
        summary="Pilot report",
        parameters=[OpenApiParameter("as_of", str, description="Day (YYYY-MM-DD); today when omitted.")],
        responses={200: PilotReportResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(PilotReportQuery, request.query_params.dict())
        body = pilot_service.report(self.channel(channel_idx), as_of=query.as_of or timezone.localdate())
        return Response(PilotReportResponse(**body).model_dump(mode="json"))
