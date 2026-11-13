# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — reports: the funnel."""

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, AdminView, parse
from django_leads.schemas.requests import FunnelQuery
from django_leads.schemas.responses import FunnelResponse
from django_leads.services import funnel_service


class FunnelView(AdminView):
    access_area = "leads.companies"

    @extend_schema(
        tags=["Leads Reports"],
        operation_id="leads_reports_funnel",
        summary="Stage moves, per-stage counts and companies per origin",
        parameters=[
            OpenApiParameter("origin", str, description="Only companies of this origin."),
            OpenApiParameter("date_from", str, description="First day of moves and creations (YYYY-MM-DD)."),
            OpenApiParameter("date_to", str, description="Last day of moves and creations (YYYY-MM-DD)."),
        ],
        responses={200: FunnelResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(FunnelQuery, request.query_params.dict())
        report = funnel_service.funnel(self.channel(channel_idx), **query.model_dump())
        return Response(FunnelResponse.model_validate(report).model_dump(mode="json"))
