# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — what a reply of each intent class does to the company's stage and contact (T4)."""

from dataclasses import asdict

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, AdminView, parse
from django_leads.enums import ReplyIntent
from django_leads.schemas.requests import ReplyRouteRequest
from django_leads.schemas.responses import ReplyRouteListResponse, ReplyRouteResponse
from django_leads.services import reply_route_service

_TAGS = ["Leads Reply Routes"]


def dump(route) -> dict:
    return ReplyRouteResponse.model_validate(asdict(route)).model_dump(mode="json")


def intent_of(value: str) -> str:
    if value not in ReplyIntent.values:
        raise NotFound("Unknown intent class.")
    return value


class ReplyRouteListView(AdminView):
    access_area = "leads.settings"

    @extend_schema(
        tags=_TAGS,
        operation_id="leads_reply_routes_list",
        summary="Stage handling per reply intent class (stored route or the safe default)",
        responses={200: ReplyRouteListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        return Response(
            {"results": [dump(route) for route in reply_route_service.effective(self.channel(channel_idx))]}
        )


class ReplyRouteDetailView(AdminView):
    access_area = "leads.settings"

    @extend_schema(
        tags=_TAGS,
        summary="Set the stage handling of one intent class",
        description="Replaces the whole route of the class. Applies to replies that arrive afterwards. The sequence "
        "and alerts of the same class are set in communicator (`reply-actions/`).",
        request=ReplyRouteRequest,
        responses={200: ReplyRouteResponse, **ERROR_RESPONSES},
    )
    def put(self, request: Request, channel_idx: str, intent: str) -> Response:
        body = parse(ReplyRouteRequest, request.data)
        channel, intent = self.channel(channel_idx), intent_of(intent)
        try:
            route = reply_route_service.upsert(
                channel, intent, action=body.action, stage_id=body.stage_id, flag_contact=body.flag_contact
            )
        except reply_route_service.ReplyRouteError as error:
            raise ValidationError({"detail": [str(error)]}) from None
        return Response(dump(route))

    @extend_schema(
        tags=_TAGS,
        summary="Back to the default for one intent class",
        request=None,
        responses={200: ReplyRouteResponse, **ERROR_RESPONSES},
    )
    def delete(self, request: Request, channel_idx: str, intent: str) -> Response:
        channel, intent = self.channel(channel_idx), intent_of(intent)
        return Response(dump(reply_route_service.reset(channel, intent)))
