# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""The tools: plan, the four direct operations, ownership, permissions, proposals for irreversible actions."""

import pytest
from django_todo.enums import Status
from django_todo.models import Task

from django_ai_crm.enums import ProposalStatus
from django_ai_crm.models import Proposal
from django_ai_crm.services import crm, plan_service, tool_service
from django_ai_crm.services.caller import ToolError
from django_leads.enums import ActivityKind
from django_leads.models import Activity
from tests.ai_crm.conftest import caller, make_deal, make_task

pytestmark = pytest.mark.django_db


def test_the_plan_lists_the_callers_overdue_and_today_tasks_in_order(anna, piotr, shop, foreign):
    late = make_task(shop, anna, "Call back", days=-2)
    today = make_task(shop, anna, "Send offer")
    make_task(shop, anna, "Next week", days=8)
    make_task(foreign, piotr, "Not mine")

    plan = plan_service.day_plan(caller(anna))

    tasks = [item for item in plan["items"] if item["type"] == "task"]
    assert [item["task_id"] for item in tasks] == [late.pk, today.pk]
    assert [item["n"] for item in tasks] == [1, 2]
    assert tasks[0]["overdue"] and not tasks[1]["overdue"]
    assert tasks[0]["company_id"] == shop.pk
    assert "1. [overdue] Call back" in plan_service.as_text(plan)


def test_close_task_records_the_outcome_with_the_ai_crm_actor_in_the_history(anna, shop):
    task = make_task(shop, anna, "Call back")

    outcome = tool_service.call(caller(anna, "cms"), "close_task", {"task_id": task.pk, "outcome": "interested"})

    task.refresh_from_db()
    assert outcome["status"] == "done"
    assert (task.status, task.outcome, task.done_by) == (Status.DONE, "interested", "anna via ai-crm (cms)")
    entry = Activity.objects.filter(company=shop, kind=ActivityKind.TASK).latest("pk")
    assert entry.actor == "anna via ai-crm (cms)"


def test_another_salespersons_task_company_and_deal_are_not_found(anna, piotr, foreign):
    task = make_task(foreign, piotr, "Not mine")
    deal = make_deal(foreign, piotr)
    me = caller(anna)
    for name, arguments in [
        ("close_task", {"task_id": task.pk}),
        ("add_note", {"company_id": foreign.pk, "text": "x"}),
        ("create_task", {"company_id": foreign.pk, "title": "x"}),
        ("move_deal", {"deal_id": deal.pk, "stage": "offer"}),
    ]:
        with pytest.raises(ToolError) as refused:
            tool_service.call(me, name, arguments)
        assert refused.value.code == "not_found", name
    task.refresh_from_db()
    assert task.status == Status.OPEN


def test_create_task_and_add_note_on_an_own_company(anna, shop):
    me = caller(anna, "mcp")

    created = tool_service.call(me, "create_task", {"company_id": shop.pk, "title": "Follow up", "due": "tomorrow"})
    noted = tool_service.call(me, "add_note", {"company_id": shop.pk, "text": "asked for a quote"})

    task = Task.objects.get(pk=created["data"]["task_id"])
    assert (task.owner_id, task.created_by, task.source) == (anna.pk, "anna via ai-crm (mcp)", "ai_crm.mcp")
    assert task.subject_ref == f"leads.Company:{shop.pk}"
    note = Activity.objects.get(pk=noted["data"]["activity_id"])
    assert (note.kind, note.message, note.actor) == (
        ActivityKind.NOTE,
        "note: asked for a quote",
        "anna via ai-crm (mcp)",
    )


def test_move_deal_to_an_open_stage_runs_now(anna, shop):
    deal = make_deal(shop, anna)

    outcome = tool_service.call(caller(anna), "move_deal", {"deal_id": deal.pk, "stage": "offer"})

    deal.refresh_from_db()
    assert outcome["status"] == "done" and deal.stage.key == "offer"
    assert Activity.objects.filter(deal=deal, actor="anna via ai-crm (api)").exists()


def test_a_lost_deal_is_only_proposed_and_runs_after_the_same_users_yes(anna, piotr, shop):
    deal = make_deal(shop, anna)
    arguments = {"deal_id": deal.pk, "stage": "lost", "lost_reason": "chose a competitor"}

    proposed = tool_service.call(caller(anna, "cms"), "move_deal", arguments)

    deal.refresh_from_db()
    assert proposed["status"] == "proposed" and deal.stage.key == "lead"
    proposal_id = proposed["data"]["proposal_id"]
    with pytest.raises(ToolError) as refused:
        tool_service.call(caller(piotr), "confirm_proposal", {"proposal_id": proposal_id})
    assert refused.value.code == "not_found"

    confirmed = tool_service.call(caller(anna, "cms"), "confirm_proposal", {"proposal_id": proposal_id})

    deal.refresh_from_db()
    assert confirmed["status"] == "done" and deal.stage.key == "lost"
    assert Proposal.objects.get(pk=proposal_id).status == ProposalStatus.CONFIRMED
    with pytest.raises(ToolError) as again:
        tool_service.call(caller(anna), "confirm_proposal", {"proposal_id": proposal_id})
    assert again.value.code == "conflict"


def test_a_lost_deal_needs_a_reason_before_it_is_proposed(anna, shop):
    deal = make_deal(shop, anna)
    with pytest.raises(ToolError) as refused:
        tool_service.call(caller(anna), "move_deal", {"deal_id": deal.pk, "stage": "lost"})
    assert refused.value.code == "invalid"
    assert not Proposal.objects.exists()


def test_do_not_contact_is_proposed_and_a_rejected_proposal_changes_nothing(anna, shop):
    proposed = tool_service.call(caller(anna), "mark_do_not_contact", {"company_id": shop.pk, "reason": "asked us"})
    rejected = tool_service.call(caller(anna), "reject_proposal", {"proposal_id": proposed["data"]["proposal_id"]})

    shop.refresh_from_db()
    assert proposed["status"] == "proposed" and rejected["status"] == "rejected"
    assert shop.do_not_contact is False

    again = tool_service.call(caller(anna), "mark_do_not_contact", {"company_id": shop.pk})
    tool_service.call(caller(anna), "confirm_proposal", {"proposal_id": again["data"]["proposal_id"]})
    shop.refresh_from_db()
    assert shop.do_not_contact is True
    assert Activity.objects.filter(company=shop, message="do not contact: on", actor="anna via ai-crm (api)").exists()


def test_sending_a_draft_is_proposed_and_sent_only_on_confirm(anna, shop, monkeypatch):
    class Draft:
        pk, subject, status = 7, "Hello", "review_required"
        thread = type("Thread", (), {"recipient_email": "ola@shop.test", "subject_ref": f"leads.Company:{shop.pk}"})

    sent = []
    monkeypatch.setattr(crm, "own_draft", lambda _caller, message_id: Draft())
    monkeypatch.setattr(crm, "approve_draft", lambda _caller, message: sent.append(message.pk) or message)

    proposed = tool_service.call(caller(anna), "approve_draft", {"message_id": 7})
    assert proposed["status"] == "proposed" and sent == []
    assert "ola@shop.test" in proposed["proposal"]["summary"]

    tool_service.call(caller(anna), "confirm_proposal", {"proposal_id": proposed["data"]["proposal_id"]})
    assert sent == [7]


def test_a_viewer_reads_the_plan_but_never_writes_through_the_agent(viewer, channel):
    from tests.ai_crm.conftest import make_company

    company = make_company(channel, "viewer.test", viewer)
    task = make_task(company, viewer, "Mine")

    plan = plan_service.day_plan(caller(viewer))
    assert [item["task_id"] for item in plan["items"] if item["type"] == "task"] == [task.pk]
    with pytest.raises(ToolError) as refused:
        tool_service.call(caller(viewer), "close_task", {"task_id": task.pk})
    assert refused.value.code == "forbidden"


def test_invalid_arguments_and_unknown_tools_are_refused(anna):
    with pytest.raises(ToolError) as unknown:
        tool_service.call(caller(anna), "drop_database", {})
    with pytest.raises(ToolError) as invalid:
        tool_service.call(caller(anna), "close_task", {"task_id": "x", "extra": 1})
    assert (unknown.value.code, invalid.value.code) == ("not_found", "invalid")


def test_every_tool_is_in_the_catalogue_with_a_json_schema():
    names = {tool["name"] for tool in tool_service.catalogue()}
    assert {"get_day_plan", "close_task", "create_task", "add_note", "move_deal", "confirm_proposal"} <= names
    assert all(tool["inputSchema"]["type"] == "object" for tool in tool_service.catalogue())
