# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Shared wiring of the admin views — auth declared explicitly, never inherited from service defaults."""

from typing import Literal, TypeVar

from django_utils.api.v2_errors import raise_pydantic_as_drf
from pydantic import BaseModel, ValidationError
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAdminUser
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from django_ai_crm.apps import AGENT_AREA
from django_ai_crm.services.caller import Caller, ToolError

SchemaT = TypeVar("SchemaT", bound=BaseModel)
ERROR_RESPONSES = {400: None, 401: None, 403: None, 404: None, 409: None}
TAGS = ["AI CRM"]


class AgentView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAdminUser]
    access_area = AGENT_AREA
    # The agent's POSTs (chat, tool calls, MCP) need only read on its area; each tool asks for the write permission
    # of the area it changes, so a Viewer can read their plan but never writes through the agent.
    access_levels = {"POST": "read"}

    @staticmethod
    def caller(request, channel_idx: str, source: str) -> Caller:
        return Caller(user=request.user, channel_idx=channel_idx, source=source)


class ToolRefused(APIException):
    def __init__(self, error: ToolError) -> None:
        self.status_code = error.status
        super().__init__(detail=error.message, code=error.code)


def parse(schema: type[SchemaT], data: object, location: Literal["body", "query"] = "body") -> SchemaT:
    """Validate request data; a Pydantic error becomes the v2 400 shape."""
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise_pydantic_as_drf(exc, location)
