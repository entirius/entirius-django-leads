# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from celery import shared_task

from django_leads.settings import QUEUE_DEFAULT


@shared_task(name="django_leads.evaluate_rules", queue=QUEUE_DEFAULT, acks_late=True)
def evaluate_rules(company_id: int, trigger: str, stage_id: int | None = None, event: str = "") -> int:
    """Worker side of the `stage_entered` receiver — `ai_pick` rules call the toolbox; `event` dedupes redelivery."""
    from django_leads.models import Company, Stage
    from django_leads.services import rule_service

    company = Company.objects.select_related("channel").filter(pk=company_id).first()
    if company is None:
        return 0
    stage = Stage.objects.filter(pk=stage_id).first() if stage_id else None
    return len(rule_service.evaluate_rules(company, trigger, stage=stage, event=event))
