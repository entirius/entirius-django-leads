# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""FIX-09b: no CSV through the broker, temp files never outlive their batch, stale batches end failed."""

import os
from datetime import timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import OperationalError
from django.utils import timezone

from django_leads.enums import ImportStatus
from django_leads.models import Company, ImportBatch
from django_leads.services import import_service, sweep_service
from django_leads.tasks import import_csv
from tests.conftest import api_url
from tests.test_import import CSV


def stored_batch(channel, content: str = CSV) -> ImportBatch:
    batch = import_service.create_batch(channel, "leads.csv", len(content.encode()), "test")
    import_service.store_upload(batch.pk, content)
    return batch


def test_import_task_args_contain_no_csv(admin_api, channel, import_tmp_dir, django_capture_on_commit_callbacks):
    upload = SimpleUploadedFile("leads.csv", CSV.encode(), content_type="text/csv")
    with mock.patch.object(import_csv, "apply_async") as queued:
        with django_capture_on_commit_callbacks(execute=True):
            batch_id = admin_api.post(api_url("imports/"), {"file": upload}, format="multipart").json()["id"]
    queued.assert_called_once_with((batch_id,))
    path = import_tmp_dir / f"{batch_id}.csv"
    assert path.read_text(encoding="utf-8") == CSV
    assert (path.stat().st_mode & 0o777, import_tmp_dir.stat().st_mode & 0o777) == (0o600, 0o700)


def test_tmp_file_deleted_after_done_and_failed(channel, import_tmp_dir):
    done, failed = stored_batch(channel), stored_batch(channel, "not,a,header\n1,2,3")
    assert import_csv.delay(done.pk).get() == ImportStatus.DONE
    assert import_csv.delay(failed.pk).get() == ImportStatus.FAILED
    assert list(import_tmp_dir.iterdir()) == []


def test_legacy_message_with_content_fails_batch_unprocessed(channel, import_tmp_dir):
    batch = import_service.create_batch(channel, "leads.csv", 0, "test")
    assert import_csv.delay(batch.pk, CSV).get() == ImportStatus.FAILED
    batch.refresh_from_db()
    assert batch.report == [{"row": 0, "reason": "legacy_message"}] and not Company.objects.exists()


def test_stale_tmp_files_swept(import_tmp_dir):
    import_tmp_dir.mkdir()
    old, fresh = import_tmp_dir / "1.csv", import_tmp_dir / "2.csv"
    old.write_text("x"), fresh.write_text("y")
    day_ago = (timezone.now() - timedelta(hours=25)).timestamp()
    os.utime(old, (day_ago, day_ago))
    assert sweep_service.sweep_tmp_files(timezone.now()) == 1
    assert list(import_tmp_dir.iterdir()) == [fresh]


def test_fail_batch_db_down_leaves_batch_for_sweeper(channel, import_tmp_dir):
    batch = stored_batch(channel)
    ImportBatch.objects.filter(pk=batch.pk).update(status=ImportStatus.RUNNING)
    down = OperationalError("database unavailable")
    with (
        mock.patch.object(import_service, "run_file", side_effect=down),
        mock.patch.object(import_service, "fail_batch", side_effect=down),
    ):
        assert import_csv.apply(args=(batch.pk,), retries=import_csv.max_retries).get() == ImportStatus.FAILED
    batch.refresh_from_db()
    assert batch.status == ImportStatus.RUNNING and list(import_tmp_dir.iterdir()) == []


def test_stale_running_batch_marked_failed(channel, import_tmp_dir):
    stale, fresh, done = stored_batch(channel), stored_batch(channel), stored_batch(channel)
    now = timezone.now()
    ImportBatch.objects.filter(pk__in=[stale.pk, fresh.pk]).update(status=ImportStatus.RUNNING)
    ImportBatch.objects.filter(pk__in=[stale.pk, done.pk]).update(modified_at=now - timedelta(minutes=31))
    ImportBatch.objects.filter(pk=done.pk).update(status=ImportStatus.DONE)
    assert sweep_service.fail_stale_batches(now) == 1
    statuses = dict(ImportBatch.objects.values_list("pk", "status"))
    assert statuses == {stale.pk: ImportStatus.FAILED, fresh.pk: ImportStatus.RUNNING, done.pk: ImportStatus.DONE}
    assert ImportBatch.objects.get(pk=stale.pk).report == [{"row": 0, "reason": "stale"}]
    assert sorted(path.name for path in import_tmp_dir.iterdir()) == sorted([f"{fresh.pk}.csv", f"{done.pk}.csv"])
