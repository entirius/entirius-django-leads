# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from celery import shared_task
from django.apps import apps
from django.db import OperationalError, transaction

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
def import_csv(self, batch_id: int, content: str) -> str:
    """Apply an uploaded CSV batch. The text travels in the message (service and worker share no disk) and
    lives in a temp file only while the run lasts; a retry resumes after the last committed row."""
    from django_leads.models import ImportBatch
    from django_leads.services import import_service

    batch = ImportBatch.objects.select_related("channel").get(pk=batch_id)
    try:
        return import_service.run_content(batch, content).status
    except OperationalError:
        if self.request.retries >= self.max_retries:
            import_service.fail_batch(batch, "database_error")
        raise


def enqueue_import(batch_id: int, content: str) -> None:
    """Queue a CSV run; the masked `argsrepr` keeps the rows out of broker and worker logs."""
    import_csv.apply_async((batch_id, content), argsrepr=f"({batch_id}, <csv>)")


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
