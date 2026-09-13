# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""`stage_entered` → stage rules, evaluated by the worker (`ai_pick` calls the toolbox)."""

from django_leads.enums import RuleTrigger
from django_leads.signals import stage_entered
from django_leads.signals._deferred import after_commit


def connect() -> None:
    stage_entered.connect(on_stage_entered, dispatch_uid="django_leads.on_stage_entered")


def on_stage_entered(sender, company, stage, **kwargs) -> None:
    from django_leads.tasks import evaluate_rules

    work = lambda: evaluate_rules.delay(company.pk, RuleTrigger.STAGE_ENTERED.value, stage.pk)  # noqa: E731
    after_commit(work, "on_stage_entered")
