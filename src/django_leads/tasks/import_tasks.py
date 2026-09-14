# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from celery import shared_task
from django.apps import apps
from django.db import OperationalError, transaction

from django_leads.enums import ImportStatus
from django_leads.settings import QUEUE_DEFAULT


@shared_task(
    bind=True,
    name="django_leads.import_csv",
    queue=QUEUE_DEFAULT,
    acks_late=True,
    max_retries=3,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
)
def import_csv(self, batch_id: int, content: str | None = None) -> str:
    """Apply the upload stored for the batch (`import_service.store_upload`); the message carries the id only.
    A retry resumes after the last committed row. `content` is only ever set by a message queued before the
    id-only contract — that batch fails as `legacy_message` without processing it."""
    from django_leads.services import import_service

    try:
        return import_service.run_upload(batch_id, legacy=content is not None).status
    except OperationalError:
        if self.request.retries < self.max_retries:
            raise
    import_service.give_up(batch_id, "database_error")
    return ImportStatus.FAILED


def enqueue_import(batch_id: int) -> None:
    """Queue the run of a batch whose upload is already stored — no row of the CSV passes through the broker."""
    import_csv.apply_async((batch_id,))


@shared_task(
    name="django_leads.import_form_lead",
    queue=QUEUE_DEFAULT,
    acks_late=True,
    max_retries=3,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
)
def import_form_lead(lead_id: int, channel_idx: str) -> bool:
    """Retry of the contact_forms bridge; idempotent by the submission id."""
    from django_leads.services import form_service

    lead = apps.get_model("django_contact_forms", "Lead").objects.filter(pk=lead_id).first()
    if lead is None:
        return False
    with transaction.atomic():
        return form_service.import_form_lead(lead, channel_idx) is not None


@shared_task(name="django_leads.fail_stale_import_batches", queue=QUEUE_DEFAULT)
def fail_stale_import_batches() -> dict[str, int]:
    """Beat every 10 min (host schedule): stale batches failed, 24 h old temp files deleted, missed form
    submissions retried once."""
    from django.utils import timezone

    from django_leads.services import sweep_service

    now = timezone.now()
    return {
        "tmp_files": sweep_service.sweep_tmp_files(now),
        "batches": sweep_service.fail_stale_batches(now),
        "form_leads": sweep_service.retry_form_leads(now),
    }
