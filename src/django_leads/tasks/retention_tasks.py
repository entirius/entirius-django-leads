# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from celery import shared_task
from celery_once import QueueOnce
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from django_leads.settings import QUEUE_DEFAULT


@shared_task(base=QueueOnce, once={"graceful": True}, name="django_leads.anonymise_inactive", queue=QUEUE_DEFAULT)
def anonymise_inactive(as_of: str | None = None) -> dict[str, int]:
    """Daily beat (host schedule): anonymised contacts per channel idx. `as_of` (ISO) moves the clock for tests."""
    from django_leads.services import retention_service

    moment = parse_datetime(as_of) if as_of else timezone.now()
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment)
    return retention_service.anonymise_inactive(moment)
