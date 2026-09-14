# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Terminal states after an outage: stale import batches fail, temp CSV files go, missed form submissions retry.

Runs from the periodic task `django_leads.fail_stale_import_batches`; every function takes `now` for tests."""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from django.apps import apps
from django.db import transaction

from django_leads import settings as leads_settings
from django_leads.enums import ActivityKind, ImportStatus
from django_leads.models import Activity, Company, ImportBatch
from django_leads.services import activity_service, form_service, import_service

logger = logging.getLogger(__name__)

TMP_FILE_MAX_AGE = timedelta(hours=24)


def stale_cutoff(now: datetime) -> datetime:
    return now - timedelta(minutes=leads_settings.LEADS_IMPORT_STALE_MINUTES)


def fail_stale_batches(now: datetime) -> int:
    """`pending`/`running` batches without progress since the cutoff end `failed` (`stale`), their file deleted."""
    stale = ImportBatch.objects.filter(
        status__in=(ImportStatus.PENDING, ImportStatus.RUNNING), modified_at__lt=stale_cutoff(now)
    )
    batches = list(stale)
    for batch in batches:
        import_service.fail_batch(batch, "stale")
        import_service.upload_path(batch.pk).unlink(missing_ok=True)
    return len(batches)


def sweep_tmp_files(now: datetime) -> int:
    """Temp CSV files older than 24 h are deleted whatever their batch — needs no database."""
    directory = Path(leads_settings.LEADS_IMPORT_TMP_DIR)
    if not directory.is_dir():
        return 0
    cutoff = (now - TMP_FILE_MAX_AGE).timestamp()
    old = [path for path in directory.glob("*.csv") if path.stat().st_mtime < cutoff]
    for path in old:
        path.unlink(missing_ok=True)
    return len(old)


def retry_form_leads(now: datetime) -> int:
    """contact_forms submissions of the last 24 h, older than the cutoff, that leads never recorded (the bridge
    and its task both gave up): imported once more; a failure is final (`form_import_failed`)."""
    if not apps.is_installed("django_contact_forms"):
        return 0
    since = now - TMP_FILE_MAX_AGE
    handled = Activity.objects.filter(
        kind__in=(ActivityKind.FORM, ActivityKind.FORM_IMPORT_FAILED), created_at__gte=since
    ).values_list("data__lead_id", flat=True)
    leads = apps.get_model("django_contact_forms", "Lead").objects.filter(
        created_at__gte=since, created_at__lt=stale_cutoff(now)
    )
    pending = list(leads.exclude(pk__in=[pk for pk in handled if pk]).select_related("contact_form__channel"))
    for lead in pending:
        _retry_form_lead(lead)
    return len(pending)


def _retry_form_lead(lead: Any) -> None:
    channel_idx = lead.contact_form.channel.idx
    try:
        with transaction.atomic():
            form_service.import_form_lead(lead, channel_idx)
    except Exception as error:
        logger.warning("leads: contact_forms lead %s failed in the sweep (%s)", lead.pk, type(error).__name__)
        _record_form_failure(lead, channel_idx)


def _record_form_failure(lead: Any, channel_idx: str) -> None:
    """Linked to the company of the submission's domain when leads already has it; otherwise the log is all."""
    company = Company.objects.filter(channel__idx=channel_idx, domain=form_service.form_domain(lead)).first()
    if company is None:
        return
    data = {"lead_id": lead.pk, "contact_form_id": lead.contact_form_id}
    activity_service.record(company, ActivityKind.FORM_IMPORT_FAILED, "form import failed", data=data, actor="form")
