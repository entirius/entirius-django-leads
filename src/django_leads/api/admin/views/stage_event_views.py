# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — the event → stage map: the effective list, set one event, reset one event."""

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, AdminView, parse
from django_leads.models import Stage
from django_leads.schemas.requests import StageEventRequest
from django_leads.schemas.responses import StageEventListResponse, StageEventResponse
from django_leads.services import stage_event_service

_TAGS = ["Leads Stage Events"]


class StageEventListView(AdminView):
    access_area = "leads.settings"

    @extend_schema(
        tags=_TAGS,
        operation_id="leads_stage_events_list",
        summary="Where each event moves a company (one entry per event)",
        responses={200: StageEventListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        entries = stage_event_service.effective(self.channel(channel_idx))
        return Response({"results": [StageEventResponse.of(entry).model_dump() for entry in entries]})


class StageEventDetailView(AdminView):
    access_area = "leads.settings"

    @extend_schema(
        tags=_TAGS,
        summary="Set where one event moves a company",
        description="A null target_stage_key keeps the stage (and stops reply:<class> falling back to reply).",
        request=StageEventRequest,
        responses={200: StageEventResponse, **ERROR_RESPONSES},
    )
    def put(self, request: Request, channel_idx: str, event: str) -> Response:
        body = parse(StageEventRequest, request.data)
        channel = self.channel(channel_idx)
        fields = {"target_stage": self.target(channel, body.target_stage_key), "forward_only": body.forward_only}
        try:
            entry = stage_event_service.upsert(channel, event, fields)
        except stage_event_service.UnknownEvent:
            raise NotFound("Event not found.") from None
        return Response(StageEventResponse.of(entry).model_dump())

    @extend_schema(
        tags=_TAGS,
        summary="Remove the event's row: it moves nothing any more",
        responses={204: None, **ERROR_RESPONSES},
    )
    def delete(self, request: Request, channel_idx: str, event: str) -> Response:
        try:
            stage_event_service.reset(self.channel(channel_idx), event)
        except stage_event_service.UnknownEvent:
            raise NotFound("Event not found.") from None
        return Response(status=204)

    @staticmethod
    def target(channel, key: str | None) -> Stage | None:
        if key is None:
            return None
        try:
            return stage_event_service.stage_by_key(channel, key)
        except Stage.DoesNotExist:
            raise ValidationError({"target_stage_key": ["unknown stage"]}) from None
