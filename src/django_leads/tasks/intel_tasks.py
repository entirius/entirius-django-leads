# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from celery import shared_task

from django_leads.settings import QUEUE_DEFAULT


@shared_task(name="django_leads.analyse_intel", queue=QUEUE_DEFAULT, acks_late=True)
def analyse_intel(audit_id: str, succeeded_sources: list[str]) -> bool:
    """One toolbox completion per run, never retried (paid, non-idempotent; budget errors stay failed)."""
    from django_leads.services import intel_service

    return intel_service.analyse_audit(audit_id, succeeded_sources) is not None
