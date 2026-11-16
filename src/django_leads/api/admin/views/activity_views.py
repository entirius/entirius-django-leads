# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — the channel history (every activity), read-only."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, PAGE_PARAMETERS, AdminView, parse
from django_leads.schemas.requests import ActivityListQuery
from django_leads.schemas.responses import ActivityActorsResponse, ActivityListResponse, HistoryEntryResponse
from django_leads.services import activity_service


class ActivityListView(AdminView):
    access_area = "leads.companies"

    @extend_schema(
        tags=["Leads Activities"],
        operation_id="leads_activities_list",
        summary="History of the channel, newest first",
        parameters=[
            OpenApiParameter("company", int, description="Company id."),
            OpenApiParameter("actor", str, description="Exact actor (username, system, …)."),
            OpenApiParameter("kind", str, description="Activity kinds, comma-separated."),
            OpenApiParameter("date_from", OpenApiTypes.DATE, description="First day, included."),
            OpenApiParameter("date_to", OpenApiTypes.DATE, description="Last day, included."),
            *PAGE_PARAMETERS,
        ],
        responses={200: ActivityListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(ActivityListQuery, request.query_params.dict())
        activities = activity_service.list_activities(
            self.channel(channel_idx),
            company_id=query.company,
            actor=query.actor,
            kinds=query.kinds,
            date_from=query.date_from,
            date_to=query.date_to,
        )
        return self.paginated(request, activities, HistoryEntryResponse.of)


class ActivityActorsView(AdminView):
    access_area = "leads.companies"

    @extend_schema(
        tags=["Leads Activities"],
        operation_id="leads_activities_actors_list",
        summary="Every actor of the channel history (the person filter)",
        responses={200: ActivityActorsResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        actors = activity_service.list_actors(self.channel(channel_idx))
        return Response(ActivityActorsResponse(results=actors).model_dump())
