# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Soft bridge to django_contact_forms: a newly created Lead becomes a leads Contact after commit."""

import logging

from django.apps import apps
from django.db import OperationalError, transaction

logger = logging.getLogger(__name__)

DISPATCH_UID = "django_leads.contact_forms_bridge"


def connect() -> bool:
    """Connect the receiver when contact_forms is installed and importable; `False` otherwise."""
    if not apps.is_installed("django_contact_forms"):
        return False
    try:
        from django_contact_forms.signals import lead_status_changed
    except ImportError:
        return False
    lead_status_changed.connect(on_lead_status_changed, dispatch_uid=DISPATCH_UID)
    return True


def on_lead_status_changed(sender, lead, old_status, channel_idx, **kwargs) -> None:
    """Creation only (`old_status is None`); the work runs after the submission commits."""
    if old_status is not None:
        return
    transaction.on_commit(lambda: _import(lead, channel_idx))


def _import(lead, channel_idx: str) -> None:
    """Atomic: a failure leaves no partial company. A retryable DB error hands the submission to the leads
    queue; any other failure is logged — it never breaks the already committed form submission."""
    from django_leads.services import form_service
    from django_leads.tasks import import_form_lead

    try:
        with transaction.atomic():
            form_service.import_form_lead(lead, channel_idx)
    except OperationalError:
        logger.warning("leads: contact_forms lead %s queued for retry", lead.pk)
        import_form_lead.delay(lead.pk, channel_idx)
    except Exception:
        logger.exception("leads: contact_forms lead %s could not be imported", lead.pk)
