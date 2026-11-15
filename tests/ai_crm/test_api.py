# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""REST API v2, the MCP endpoint and the access declarations."""

import json

import pytest
from django_access.testing import assert_routes_covered
from django_todo.enums import Status

from tests.ai_crm.conftest import api_for, make_task, url

pytestmark = pytest.mark.django_db


def test_admin_routes_are_covered():
    assert_routes_covered("django_ai_crm", require_own=True)


def test_the_plan_needs_a_staff_token(client, anna):
    assert client.get(url("plan/")).status_code == 401


def test_the_plan_of_the_token_holder_with_numbered_text(anna, piotr, shop, foreign):
    make_task(shop, anna, "Call back")
    make_task(foreign, piotr, "Not mine")

    body = api_for(anna).get(url("plan/"), {"language": "pl"}).json()

    assert [item["title"] for item in body["items"] if item["type"] == "task"] == ["Call back"]
    assert body["text"].startswith("Twój dzień, po kolei:")


def test_a_tool_call_runs_with_the_declared_source(anna, shop):
    task = make_task(shop, anna, "Call back")

    response = api_for(anna).post(
        url("tools/close_task/"), {"arguments": {"task_id": task.pk, "outcome": "ok"}, "source": "cms"}, "json"
    )

    assert response.status_code == 200, response.content
    task.refresh_from_db()
    assert (task.status, task.done_by) == (Status.DONE, "anna via ai-crm (cms)")


def test_a_refused_tool_call_answers_with_its_status(anna, piotr, foreign):
    task = make_task(foreign, piotr, "Not mine")
    api = api_for(anna)
    assert api.post(url("tools/close_task/"), {"arguments": {"task_id": task.pk}}, "json").status_code == 404
    assert api.post(url("tools/close_task/"), {"arguments": {}}, "json").status_code == 400
    assert api.post(url("tools/nope/"), {}, "json").status_code == 404
    assert api.post(url("tools/close_task/"), {"source": "mcp"}, "json").status_code == 400


def test_a_user_without_a_role_is_stopped_by_the_gate(db):
    from tests.ai_crm.conftest import make_user

    assert api_for(make_user("norole", role=None)).get(url("plan/")).status_code == 403


def test_the_proposals_list_shows_only_the_callers_pending_ones(anna, piotr, shop):
    api_for(anna).post(url("tools/mark_do_not_contact/"), {"arguments": {"company_id": shop.pk}}, "json")

    assert len(api_for(anna).get(url("proposals/")).json()["results"]) == 1
    assert api_for(piotr).get(url("proposals/")).json()["results"] == []


# --- MCP -------------------------------------------------------------------------------------------------------------


def rpc(api, method: str, params: dict | None = None, msg_id: int | None = 1):
    message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if msg_id is not None:
        message["id"] = msg_id
    return api.post(
        url("mcp/"),
        json.dumps(message),
        content_type="application/json",
        HTTP_ACCEPT="application/json, text/event-stream",
    )


def test_mcp_initialize_list_and_call_with_the_salespersons_token(anna, shop):
    task = make_task(shop, anna, "Call back")
    api = api_for(anna)

    init = rpc(api, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}})
    assert init.status_code == 200
    assert init.json()["result"]["protocolVersion"] == "2025-06-18"
    assert init.json()["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert rpc(api, "notifications/initialized", msg_id=None).status_code == 202

    names = [tool["name"] for tool in rpc(api, "tools/list").json()["result"]["tools"]]
    assert "get_day_plan" in names and "close_task" in names

    plan = rpc(api, "tools/call", {"name": "get_day_plan", "arguments": {}}).json()["result"]
    assert plan["isError"] is False and "Call back" in plan["content"][0]["text"]

    done = rpc(api, "tools/call", {"name": "close_task", "arguments": {"task_id": task.pk, "outcome": "booked"}})
    assert done.json()["result"]["isError"] is False
    task.refresh_from_db()
    assert task.done_by == "anna via ai-crm (mcp)"


def test_mcp_tool_errors_and_protocol_errors(anna, piotr, foreign):
    task = make_task(foreign, piotr, "Not mine")
    api = api_for(anna)

    refused = rpc(api, "tools/call", {"name": "close_task", "arguments": {"task_id": task.pk}}).json()["result"]
    assert refused["isError"] is True and refused["structuredContent"]["code"] == "not_found"
    assert rpc(api, "resources/list").json()["error"]["code"] == -32601
    assert rpc(api, "tools/call", {"name": "nope"}).json()["error"]["code"] == -32602
    broken = api.post(url("mcp/"), "{not json", content_type="application/json")
    assert broken.json()["error"]["code"] == -32700


def test_mcp_without_a_token_is_401_and_get_is_405(client, anna):
    assert client.post(url("mcp/"), "{}", content_type="application/json").status_code == 401
    assert api_for(anna).get(url("mcp/")).status_code == 405


def test_the_openapi_schema_lists_every_route():
    from drf_spectacular.generators import SchemaGenerator

    paths = SchemaGenerator().get_schema(request=None, public=True)["paths"]
    prefix = "/api/ai-crm/v2/admin/{channel_idx}/"
    assert {f"{prefix}{p}" for p in ("plan/", "tools/", "tools/{name}/", "proposals/", "chat/", "mcp/")} <= set(paths)


def test_every_plan_item_carries_every_field_and_the_spec_is_typed(anna, shop):
    make_task(shop, anna, "Call back")
    item = api_for(anna).get(url("plan/")).json()["items"][0]
    assert {"n", "type", "task_id", "title", "due_at", "overdue", "company_id", "count"} <= set(item)
    assert item["count"] is None and item["task_id"]
