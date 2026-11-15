# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""leads → prospecting: `contact_needed` (a company lost its last person to mail: wrong person or opted out)
starts a people search. Connected only when leads is installed and sends the signal."""

import logging

from django.apps import apps

logger = logging.getLogger(__name__)


def connect() -> None:
    if not apps.is_installed("django_leads"):
        return
    from django_leads import signals as leads_signals

    signal = getattr(leads_signals, "contact_needed", None)
    if signal is not None:
        signal.connect(on_contact_needed, dispatch_uid="django_prospecting.on_contact_needed")


def on_contact_needed(sender, subject_ref, channel_idx, company_name, domain, reason, **kwargs) -> None:
    from django_prospecting.services import search_service

    try:
        search_service.start(
            channel_idx=channel_idx, subject_ref=subject_ref, company_name=company_name, domain=domain,
            reason=reason, requested_by="leads",
        )  # fmt: skip
    except Exception:
        logger.exception("prospecting: receiver on_contact_needed failed")
