# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from celery import shared_task
from celery_once import QueueOnce

from django_leads import settings as leads_settings
from django_leads.settings import QUEUE_DEFAULT


@shared_task(base=QueueOnce, once={"graceful": True}, name="django_leads.resume_contacts", queue=QUEUE_DEFAULT)
def resume_contacts() -> dict:
    """Daily beat (Q20): drafts again to the contacts whose "not now" day has come. Does nothing until
    `LEADS_RESUME_ENABLED`; every draft passes the outreach gate."""
    from django_leads.services import resume_service

    if not leads_settings.LEADS_RESUME_ENABLED:
        return {"enabled": False}
    return {"enabled": True, **resume_service.run_due()}


@shared_task(base=QueueOnce, once={"graceful": True}, name="django_leads.send_digest", queue=QUEUE_DEFAULT)
def send_digest() -> dict:
    """Daily beat (Q16): the digest of what the automation did and refused. Does nothing until `LEADS_DIGEST_ENABLED`."""
    from django_leads.services import digest_service

    if not leads_settings.LEADS_DIGEST_ENABLED:
        return {"enabled": False}
    return {"enabled": True, "sent": digest_service.send_all()}
