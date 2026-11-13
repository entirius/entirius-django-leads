# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Import with `run_rules`: created companies enter their first stage after the chunk commits; `origin`."""

import io
from types import SimpleNamespace
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError

from django_leads.enums import ImportStatus, RuleTrigger
from django_leads.models import Company, ImportBatch, Stage
from django_leads.services import import_service
from tests.conftest import api_url
from tests.test_import import HEADER

pytestmark = pytest.mark.django_db

ROWS = "\n".join(
    [
        f"{HEADER},origin",
        "A,a-shop.test,,,,Ann,A,ann@a-shop.test,,pl,legitimate_interest,,fair",
        "B,b-shop.test,,,,Bob,B,bob@b-shop.test,,pl,legitimate_interest,,fair",
        "C,c-shop.test,,,,Cy,C,cy@c-shop.test,,pl,legitimate_interest,,",
    ]
)


def run(channel, *, run_rules: bool, capture) -> tuple[ImportBatch, mock.MagicMock]:
    batch = import_service.create_batch(channel, "leads.csv", len(ROWS), "test", run_rules=run_rules)
    with mock.patch("django_leads.tasks.evaluate_rules.delay") as evaluate, capture(execute=True):
        batch = import_service.run_content(batch, ROWS)
    return batch, evaluate


def test_run_rules_queues_one_evaluation_per_created_company(channel, django_capture_on_commit_callbacks):
    batch, evaluate = run(channel, run_rules=True, capture=django_capture_on_commit_callbacks)
    assert batch.status == ImportStatus.DONE and evaluate.call_count == 3
    first = Stage.objects.get(key="new")
    ids = sorted(Company.objects.values_list("pk", flat=True))
    assert sorted(call.args[0] for call in evaluate.call_args_list) == ids
    assert {call.args[1:3] for call in evaluate.call_args_list} == {(RuleTrigger.STAGE_ENTERED.value, first.pk)}


def test_without_the_flag_nothing_is_queued(channel, django_capture_on_commit_callbacks):
    assert run(channel, run_rules=False, capture=django_capture_on_commit_callbacks)[1].call_count == 0


def test_resumed_chunk_queues_nothing_extra(channel, django_capture_on_commit_callbacks):
    batch, _ = run(channel, run_rules=True, capture=django_capture_on_commit_callbacks)
    ImportBatch.objects.filter(pk=batch.pk).update(status=ImportStatus.RUNNING, last_row_done=0)
    batch.refresh_from_db()
    with mock.patch("django_leads.tasks.evaluate_rules.delay") as evaluate:
        with django_capture_on_commit_callbacks(execute=True):
            batch = import_service.run_content(batch, ROWS)
    assert batch.matched_count == 3 and evaluate.call_count == 0


def test_rolled_back_bulk_write_fires_only_for_rows_written_alone(
    channel, monkeypatch, django_capture_on_commit_callbacks
):
    real = import_service._ChunkWriter.flush

    def flush_then_fail(writer):
        real(writer)
        raise IntegrityError("race")

    monkeypatch.setattr(import_service._ChunkWriter, "flush", flush_then_fail)
    batch, evaluate = run(channel, run_rules=True, capture=django_capture_on_commit_callbacks)
    assert batch.created_count == 3 and evaluate.call_count == 3


def test_run_rules_drafts_for_created_companies(channel, make_rule, django_capture_on_commit_callbacks):
    make_rule(stage=Stage.objects.get(key="new"), require_hooks=False)
    message = SimpleNamespace(pk=7, status="review_required")
    batch = import_service.create_batch(channel, "leads.csv", len(ROWS), "test", run_rules=True)
    with (
        mock.patch("django_leads.services.outreach_service.communicate", return_value=message) as communicate,
        mock.patch("django_leads.services.outreach_service.render_legal_footer", return_value="footer"),
        mock.patch("django_leads.services.outreach_service.resolve_clause_set"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        import_service.run_content(batch, ROWS)
    assert sorted(call.kwargs["recipient"].email for call in communicate.call_args_list) == [
        "ann@a-shop.test",
        "bob@b-shop.test",
        "cy@c-shop.test",
    ]


def test_upload_takes_run_rules(channel, admin_api, django_capture_on_commit_callbacks):
    def upload(**fields):
        file = SimpleUploadedFile("leads.csv", ROWS.encode(), content_type="text/csv")
        with mock.patch("django_leads.api.admin.views.import_views.enqueue_import"):
            return admin_api.post(api_url("imports/"), {"file": file, **fields}, format="multipart").json()

    assert upload()["run_rules"] is False
    body = upload(run_rules="true")
    assert body["run_rules"] is True and ImportBatch.objects.get(pk=body["id"]).run_rules


def test_batch_report_shows_the_queued_evaluations(channel, admin_api, django_capture_on_commit_callbacks):
    batch, _ = run(channel, run_rules=True, capture=django_capture_on_commit_callbacks)
    body = admin_api.get(api_url(f"imports/{batch.pk}/")).json()
    assert (body["run_rules"], body["rules_queued"]) == (True, 3)


def test_command_takes_run_rules(channel, tmp_path, django_capture_on_commit_callbacks):
    path = tmp_path / "leads.csv"
    path.write_text(ROWS, encoding="utf-8")
    out = io.StringIO()
    with mock.patch("django_leads.tasks.evaluate_rules.delay"), django_capture_on_commit_callbacks(execute=True):
        call_command("leads_import_csv", channel=channel.idx, file=str(path), sync=True, run_rules=True, stdout=out)
    assert ImportBatch.objects.get().run_rules and "rules_queued=3" in out.getvalue()


# --- origin ------------------------------------------------------------------------------------------------------


def test_origin_column_fills_only_an_empty_origin(channel, django_capture_on_commit_callbacks):
    Company.objects.create(
        channel=channel,
        name="A",
        domain="a-shop.test",
        stage=Stage.objects.get(key="new"),
        stage_entered_at="2026-01-01T00:00:00Z",
        source="csv",
        origin="list",
    )
    run(channel, run_rules=False, capture=django_capture_on_commit_callbacks)
    assert dict(Company.objects.values_list("domain", "origin")) == {
        "a-shop.test": "list",
        "b-shop.test": "fair",
        "c-shop.test": "",
    }


def test_origin_is_editable_and_filters_the_list(channel, admin_api, django_capture_on_commit_callbacks):
    run(channel, run_rules=False, capture=django_capture_on_commit_callbacks)
    company = Company.objects.get(domain="c-shop.test")
    response = admin_api.patch(api_url(f"companies/{company.pk}/"), {"origin": "fair"}, format="json")
    assert response.status_code == 200 and response.json()["origin"] == "fair"
    rows = admin_api.get(api_url("companies/?origin=fair")).json()["results"]
    assert sorted(row["domain"] for row in rows) == ["a-shop.test", "b-shop.test", "c-shop.test"]
    assert admin_api.get(api_url("companies/?origin=none")).json()["count"] == 0
