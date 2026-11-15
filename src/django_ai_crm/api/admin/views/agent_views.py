# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Day plan, tool calls, proposals and the chat — REST API v2 of the agent."""

import zoneinfo

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from django_ai_crm.api.admin.views._base import ERROR_RESPONSES, TAGS, AgentView, ToolRefused, parse
from django_ai_crm.enums import Source
from django_ai_crm.schemas.requests import ChatBody, PlanQuery, ToolCallBody
from django_ai_crm.services import chat_service, plan_service, proposal_service, tool_service
from django_ai_crm.services.caller import ToolError


class PlanView(AgentView):
    @extend_schema(
        operation_id="ai_crm_plan",
        tags=TAGS,
        summary="The caller's day plan",
        description="The caller's overdue and today tasks, the drafts and replies waiting, the companies due to come "
        "back — numbered in order, as the Today screen; `text` is the same as numbered lines.",
        parameters=[OpenApiParameter("tz", str), OpenApiParameter("language", str, enum=["en", "pl"])],
        responses={200: OpenApiTypes.OBJECT, **ERROR_RESPONSES},
    )
    def get(self, request, channel_idx: str):
        query = parse(PlanQuery, request.query_params.dict(), "query")
        try:
            zone = zoneinfo.ZoneInfo(query.tz) if query.tz else None
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            raise DRFValidationError({"tz": ["Unknown time zone."]}) from None
        plan = plan_service.day_plan(self.caller(request, channel_idx, Source.API), zone)
        return Response({**plan, "text": plan_service.as_text(plan, query.language)})


class ToolListView(AgentView):
    @extend_schema(
        operation_id="ai_crm_tools",
        tags=TAGS,
        summary="The agent's tools",
        description="Name, description and JSON Schema of the arguments of every tool — the same list as MCP.",
        responses={200: OpenApiTypes.OBJECT, **ERROR_RESPONSES},
    )
    def get(self, request, channel_idx: str):
        return Response({"results": tool_service.catalogue()})


class ToolCallView(AgentView):
    @extend_schema(
        operation_id="ai_crm_tool_call",
        tags=TAGS,
        summary="Call one tool",
        description="Runs the tool, or for an irreversible action stores a proposal (`status` `proposed`) that runs "
        "only through `confirm_proposal`. Refusals: 400 invalid arguments, 403 permission, 404 not yours or missing, "
        "409 state, 503 module not installed.",
        request=OpenApiTypes.OBJECT,
        responses={200: OpenApiTypes.OBJECT, 503: None, **ERROR_RESPONSES},
    )
    def post(self, request, channel_idx: str, name: str):
        body = parse(ToolCallBody, request.data)
        caller = self.caller(request, channel_idx, body.source)
        try:
            return Response(tool_service.call(caller, name, body.arguments, body.language))
        except ToolError as exc:
            raise ToolRefused(exc) from None


class ProposalListView(AgentView):
    @extend_schema(
        operation_id="ai_crm_proposals",
        tags=TAGS,
        summary="The caller's proposals waiting for a yes or no",
        responses={200: OpenApiTypes.OBJECT, **ERROR_RESPONSES},
    )
    def get(self, request, channel_idx: str):
        caller = self.caller(request, channel_idx, Source.API)
        return Response({"results": [proposal_service.dump(p) for p in proposal_service.pending(caller)]})


class ChatView(AgentView):
    @extend_schema(
        operation_id="ai_crm_chat",
        tags=TAGS,
        summary="One turn of the conversation with the agent",
        description="The salesperson's message → the agent's reply, the tool results, the open proposals and the "
        "fresh day plan. Short commands run without a model; the rest goes to the AI toolbox model (AI_CRM_MODEL).",
        request=OpenApiTypes.OBJECT,
        responses={200: OpenApiTypes.OBJECT, **ERROR_RESPONSES},
    )
    def post(self, request, channel_idx: str):
        body = parse(ChatBody, request.data)
        caller = self.caller(request, channel_idx, body.source)
        history = [turn.model_dump() for turn in body.history]
        return Response(chat_service.chat(caller, body.message, language=body.language, history=history))
