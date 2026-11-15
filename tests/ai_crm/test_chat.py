# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""The conversation: short commands, confirmations, the toolbox model (mocked)."""

import json

import httpx
import pytest
from django_todo.enums import Status
from django_todo.models import Task
from django_utils.toolbox.testing import CANNED_COMPLETION, error_response, mock_toolbox

from django_ai_crm.services import chat_service
from django_leads.models import Activity
from tests.ai_crm.conftest import api_for, caller, make_deal, make_task, url

pytestmark = pytest.mark.django_db


def say(user, message: str, language: str = "en", source: str = "cms") -> dict:
    return chat_service.chat(caller(user, source), message, language=language)


def test_plan_command_lists_the_day(anna, shop):
    make_task(shop, anna, "Call back")
    assert "1. [today] Call back" in say(anna, "plan")["reply"]
    assert say(anna, "co dalej?", "pl")["reply"].startswith("Twój dzień")


def test_done_with_a_plan_number_closes_that_task(anna, shop):
    first = make_task(shop, anna, "Call back", days=-1)
    second = make_task(shop, anna, "Send offer")

    turn = say(anna, "zrobione 2: wysłałem ofertę", "pl")

    second.refresh_from_db()
    first.refresh_from_db()
    assert (second.status, second.outcome, second.done_by) == (Status.DONE, "wysłałem ofertę", "anna via ai-crm (cms)")
    assert first.status == Status.OPEN
    assert turn["reply"].startswith("Gotowe: zadanie")
    assert [item["task_id"] for item in turn["plan"]["items"] if item["type"] == "task"] == [first.pk]


def test_note_and_next_task_on_the_company_of_a_plan_item(anna, shop):
    make_task(shop, anna, "Call back")

    say(anna, "note 1: asked for a quote")
    say(anna, "task 1 tomorrow: send the quote")

    assert Activity.objects.filter(
        company=shop, message="note: asked for a quote", actor="anna via ai-crm (cms)"
    ).exists()
    created = Task.objects.get(title="send the quote")
    assert created.owner_id == anna.pk and created.due_at is not None


def test_a_lost_deal_asks_first_and_yes_runs_it(anna, shop):
    deal = make_deal(shop, anna)

    asked = say(anna, f"deal #{deal.pk} -> lost: budget cut")
    deal.refresh_from_db()
    assert deal.stage.key == "lead" and len(asked["proposals"]) == 1
    assert "cannot be undone" in asked["reply"]

    yes = say(anna, "yes")
    deal.refresh_from_db()
    assert deal.stage.key == "lost" and yes["proposals"] == []


def test_no_drops_the_latest_proposal_and_yes_without_one_says_so(anna, shop):
    say(anna, "dnc 1", "en")  # no plan item 1 → nothing proposed
    say(anna, f"dnc #{shop.pk}: asked to stop")
    assert say(anna, "no")["proposals"] == []
    shop.refresh_from_db()
    assert shop.do_not_contact is False
    assert say(anna, "tak", "pl")["reply"] == "Nic nie czeka na Twoje potwierdzenie."


def test_another_salespersons_task_by_id_is_not_found(anna, piotr, foreign):
    task = make_task(foreign, piotr, "Not mine")
    turn = say(anna, f"done #{task.pk}: hacked")
    task.refresh_from_db()
    assert task.status == Status.OPEN and turn["results"][0]["code"] == "not_found"


def test_free_text_without_a_model_explains_the_commands(anna, settings):
    settings.AI_CRM_MODEL = ""
    turn = say(anna, "I phoned them and they want a meeting")
    assert turn["reply"].startswith("I did not understand") and turn["used_model"] is False


def test_free_text_goes_to_the_model_and_its_actions_run_through_the_tools(anna, shop, settings):
    settings.AI_CRM_MODEL = "fake-chat"
    task = make_task(shop, anna, "Call back")
    deal = make_deal(shop, anna)
    answer = {
        "reply": "Noted.",
        "actions": [
            {"tool": "close_task", "arguments": {"task_id": task.pk, "outcome": "wants a meeting"}},
            {"tool": "move_deal", "arguments": {"deal_id": deal.pk, "stage": "lost", "lost_reason": "x"}},
            {"tool": "confirm_proposal", "arguments": {"proposal_id": 1}},
        ],
    }
    with mock_toolbox() as router:
        route = router["complete"].mock(
            return_value=httpx.Response(200, json={**CANNED_COMPLETION, "parsed": answer, "output": json.dumps(answer)})
        )
        turn = say(anna, "I phoned them and they want a meeting")

    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == "fake-chat" and "ai_crm.chat" in sent["tags"]
    task.refresh_from_db()
    deal.refresh_from_db()
    assert task.status == Status.DONE and task.done_by == "anna via ai-crm (cms)"
    # the model's lost deal waits for the salesperson; its own confirm_proposal is ignored
    assert deal.stage.key == "lead" and len(turn["proposals"]) == 1
    assert [result["tool"] for result in turn["results"]] == ["close_task", "move_deal"]


def test_an_unusable_or_failed_model_answer_falls_back(anna, settings):
    settings.AI_CRM_MODEL = "fake-chat"
    with mock_toolbox() as router:
        turn = say(anna, "hello there")  # the canned {"ok": true} is not a reply
        router["complete"].mock(return_value=error_response(402, "BUDGET_EXCEEDED"))
        failed = say(anna, "hello again")
    assert turn["used_model"] and turn["reply"].startswith("I did not understand")
    assert failed["reply"].startswith("I did not understand")


def test_chat_endpoint(anna, shop):
    make_task(shop, anna, "Call back")
    response = api_for(anna).post(
        url("chat/"), {"message": "zrobione 1: ok", "language": "pl", "source": "cms"}, "json"
    )
    assert response.status_code == 200
    assert response.json()["reply"].startswith("Gotowe")
    assert api_for(anna).post(url("chat/"), {"message": ""}, "json").status_code == 400
