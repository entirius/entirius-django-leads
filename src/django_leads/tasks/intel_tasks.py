# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from celery import shared_task

from django_leads.settings import QUEUE_DEFAULT


@shared_task(bind=True, name="django_leads.analyse_intel", queue=QUEUE_DEFAULT, acks_late=True)
def analyse_intel(self, audit_id: str, succeeded_sources: list[str]) -> bool:
    """One toolbox completion per run, never retried by Celery (paid; budget errors stay failed) — only a transient
    failure is retried, by `retry_failed_analyses`. A redelivered message keeps its task id — the claim
    `intel:<audit>:<task id>` stops a second completion."""
    from django_leads.services import intel_service

    return intel_service.analyse_audit(audit_id, succeeded_sources, run_id=self.request.id or "") is not None


@shared_task(name="django_leads.retry_failed_analyses", queue=QUEUE_DEFAULT)
def retry_failed_analyses() -> dict[str, int]:
    """Beat every 10 min (host schedule): transiently failed analyses run again once the toolbox is reachable.
    Idempotent — each claim moves `retry → claimed` by compare-and-set before its one completion."""
    from django_leads.services import intel_service

    return intel_service.retry_failed_analyses()
