# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from celery import shared_task
from celery_once import QueueOnce

from django_leads.settings import QUEUE_DEFAULT


@shared_task(base=QueueOnce, once={"graceful": True}, name="django_leads.reverify_addresses", queue=QUEUE_DEFAULT)
def reverify_addresses() -> dict:
    """Daily beat: hard bounces of communicator first (always), then — with `LEADS_VERIFY_ENABLED` — the next batch of
    unverified or stale addresses. Returns the counts per status and the number of bounces marked."""
    from django_leads.services import verification_service

    bounced = verification_service.sync_bounces()
    return {"bounced": bounced, "checked": verification_service.reverify_stale()}
