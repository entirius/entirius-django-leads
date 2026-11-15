# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""The MCP endpoint (streamable HTTP): POST JSON-RPC, JSON answers; GET (no event stream) 405."""

from django.http import HttpResponse, JsonResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.parsers import BaseParser

from django_ai_crm.api.admin.views._base import ERROR_RESPONSES, TAGS, AgentView
from django_ai_crm.enums import Source
from django_ai_crm.services import mcp_service


class RawJsonParser(BaseParser):
    """Leaves the body to the JSON-RPC handler: a parse error is a JSON-RPC error, not a DRF 400."""

    media_type = "*/*"

    def parse(self, stream, media_type=None, parser_context=None):
        return stream.read() if stream is not None else b""


class McpView(AgentView):
    parser_classes = [RawJsonParser]

    @extend_schema(
        operation_id="ai_crm_mcp",
        tags=TAGS,
        summary="MCP server (JSON-RPC 2.0)",
        description="Model Context Protocol, streamable HTTP transport without sessions or event streams: "
        "initialize, ping, tools/list, tools/call. The tools are the REST API's. Notifications answer 202.",
        request=OpenApiTypes.OBJECT,
        responses={200: OpenApiTypes.OBJECT, 202: None, **ERROR_RESPONSES},
    )
    def post(self, request, channel_idx: str):
        answer = mcp_service.handle(self.caller(request, channel_idx, Source.MCP), request.data)
        if answer is None:
            return HttpResponse(status=202)
        return JsonResponse(answer, safe=False)
