# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""MCP server (Model Context Protocol, streamable HTTP transport): JSON-RPC 2.0 over POST, answered as JSON.

Stateless: no session id, no server-sent events, no server-initiated requests. The tools are `tool_service`'s —
the same as the CMS chat and the REST API. Clients: Claude Code, ChatGPT connectors, any MCP client.
"""

import json
from typing import Any

from django_ai_crm import __version__
from django_ai_crm.services import tool_service
from django_ai_crm.services.caller import Caller, ToolError

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "entirius-ai-crm", "title": "Entirius AI CRM", "version": __version__}
INSTRUCTIONS = (
    "Entirius AI CRM of one salesperson. Start with get_day_plan, lead the salesperson through the items in order and "
    "record what they did with close_task, add_note, create_task and move_deal. Irreversible actions (lost deal, Do "
    "not contact, sending a draft) come back as proposals: ask the salesperson and call confirm_proposal only after "
    "their explicit yes."
)
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602


def handle(caller: Caller, body: bytes) -> Any | None:
    """The JSON-RPC answer to a request or a batch; None when nothing needs an answer (notifications only)."""
    try:
        payload = json.loads(body or b"null")
    except ValueError:
        return _error(None, PARSE_ERROR, "Parse error")
    if isinstance(payload, list):
        answers = [answer for message in payload if (answer := _message(caller, message)) is not None]
        return answers or None
    return _message(caller, payload)


def _message(caller: Caller, message: Any) -> dict | None:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        if isinstance(message, dict) and "method" not in message and ("result" in message or "error" in message):
            return None  # a client's answer to a server request — this server sends none
        return _error(message.get("id") if isinstance(message, dict) else None, INVALID_REQUEST, "Invalid Request")
    if "id" not in message:
        return None  # a notification (notifications/initialized, notifications/cancelled …)
    msg_id, method, params = message["id"], message["method"], message.get("params") or {}
    handler = METHODS.get(method)
    if handler is None:
        return _error(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")
    if not isinstance(params, dict):
        return _error(msg_id, INVALID_PARAMS, "params must be an object")
    return handler(caller, msg_id, params)


def _initialize(_caller: Caller, msg_id: Any, params: dict) -> dict:
    asked = params.get("protocolVersion")
    version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
    return _result(
        msg_id,
        {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        },
    )


def _ping(_caller: Caller, msg_id: Any, _params: dict) -> dict:
    return _result(msg_id, {})


def _tools_list(_caller: Caller, msg_id: Any, _params: dict) -> dict:
    return _result(msg_id, {"tools": tool_service.catalogue()})


def _tools_call(caller: Caller, msg_id: Any, params: dict) -> dict:
    name, arguments = params.get("name"), params.get("arguments") or {}
    if not isinstance(name, str) or not isinstance(arguments, dict):
        return _error(msg_id, INVALID_PARAMS, "tools/call needs a name and an arguments object")
    if name not in tool_service.TOOLS:
        return _error(msg_id, INVALID_PARAMS, f"Unknown tool: {name}")
    try:
        outcome = tool_service.call(caller, name, arguments)
    except ToolError as exc:
        error = {"status": "error", "code": exc.code, "message": exc.message}
        return _result(msg_id, _content(error, is_error=True))
    return _result(msg_id, _content(outcome, is_error=outcome.get("status") == tool_service.FAILED))


def _content(outcome: dict, *, is_error: bool) -> dict:
    """The message for the model to read, the whole result as structured content."""
    return {
        "content": [{"type": "text", "text": outcome["message"]}],
        "structuredContent": outcome,
        "isError": is_error,
    }


def _result(msg_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


METHODS = {"initialize": _initialize, "ping": _ping, "tools/list": _tools_list, "tools/call": _tools_call}
