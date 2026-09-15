# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Soft notifications: a human is asked for attention only when django_notifications is installed."""

import functools
import logging

from django_leads.models import Company
from django_leads.settings import LEADS_NOTIFY_ROLE

logger = logging.getLogger(__name__)


@functools.cache
def _notifications_available() -> bool:
    try:
        from django_notifications.services.notify_service import notify  # noqa: F401
    except (ImportError, RuntimeError):
        logger.warning("django_notifications not installed — leads alerts are logged only")
        return False
    return True


def alert(company: Company, *, severity: str, title: str, body: str = "") -> None:
    """Never raises: a missing notifications channel is logged."""
    if not _notifications_available():
        return
    from django_notifications.services.notify_service import notify

    try:
        notify(
            channel_idx=company.channel.idx,
            recipient_role=LEADS_NOTIFY_ROLE,
            severity=severity,
            subject_ref=f"leads.Company:{company.pk}",
            title=title,
            body=body,
        )
    except Exception:
        logger.exception("leads: notification for company %s failed", company.pk)
