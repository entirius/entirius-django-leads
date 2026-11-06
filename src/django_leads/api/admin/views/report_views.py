# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — the funnel report."""

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, AdminView, parse
from django_leads.schemas.requests import FunnelQuery
from django_leads.schemas.responses import FunnelResponse
from django_leads.services import funnel_service


class FunnelReportView(AdminView):
    access_area = "leads.companies"

    @extend_schema(
        tags=["Leads Reports"],
        summary="Funnel: transitions between stages and time spent in a stage",
        description="Built from the `stage` timeline entries. `origin` filters companies (empty value = no origin, "
        "omitted = any); `date_from` / `date_to` bound the transition time. Companies still in a stage are counted "
        "in `current` but not in the time averages.",
        parameters=[
            OpenApiParameter("origin", str, description="Exact company origin; empty = without origin."),
            OpenApiParameter("date_from", str, description="ISO 8601 datetime with offset."),
            OpenApiParameter("date_to", str, description="ISO 8601 datetime with offset."),
        ],
        responses={200: FunnelResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(FunnelQuery, request.query_params.dict())
        body = funnel_service.report(self.channel(channel_idx), **query.model_dump())
        return Response(FunnelResponse(**body).model_dump())
