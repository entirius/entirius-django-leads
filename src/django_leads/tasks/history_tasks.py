# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from celery import shared_task
from celery_once import QueueOnce
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from django_leads.settings import QUEUE_DEFAULT


@shared_task(base=QueueOnce, once={"graceful": True}, name="django_leads.purge_history", queue=QUEUE_DEFAULT)
def purge_history(as_of: str | None = None) -> dict[str, int]:
    """Daily beat (host schedule): deleted history entries per channel idx. `as_of` (ISO) moves the clock for tests."""
    from django_leads.services import activity_service

    moment = parse_datetime(as_of) if as_of else timezone.now()
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment)
    return activity_service.purge_old(moment)
