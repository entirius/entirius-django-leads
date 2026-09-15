# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — activity timeline, read-only."""

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, PAGE_PARAMETERS, AdminView, parse
from django_leads.schemas.requests import ActivityListQuery
from django_leads.schemas.responses import ActivityListResponse, ActivityResponse
from django_leads.services import activity_service


class ActivityListView(AdminView):
    @extend_schema(
        tags=["Leads activities"],
        operation_id="leads_activities_list",
        summary="Timeline of the channel, newest first",
        parameters=[OpenApiParameter("company", int, description="Company id."), *PAGE_PARAMETERS],
        responses={200: ActivityListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(ActivityListQuery, request.query_params.dict())
        activities = activity_service.list_activities(self.channel(channel_idx), company_id=query.company)
        return self.paginated(request, activities, ActivityResponse.model_validate)
