# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from celery import shared_task
from django.db import OperationalError

from django_leads.settings import QUEUE_DEFAULT


@shared_task(
    name="django_leads.import_csv",
    queue=QUEUE_DEFAULT,
    acks_late=True,
    max_retries=3,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
)
def import_csv(batch_id: int) -> str:
    """Apply an uploaded CSV batch; a retry restarts the batch (the import is idempotent)."""
    from django_leads.models import ImportBatch
    from django_leads.services import import_service

    batch = import_service.run_batch(ImportBatch.objects.select_related("channel").get(pk=batch_id))
    return batch.status
