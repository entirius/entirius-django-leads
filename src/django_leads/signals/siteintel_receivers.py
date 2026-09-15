# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""siteintel `report_ready` → intel analysis in the worker."""

from django_siteintel.signals import report_ready

from django_leads.signals._deferred import after_commit


def connect() -> None:
    report_ready.connect(on_report_ready, dispatch_uid="django_leads.on_report_ready")


def on_report_ready(sender, audit, succeeded_sources, **kwargs) -> None:
    from django_leads.tasks import analyse_intel

    work = lambda: analyse_intel.delay(str(audit.pk), list(succeeded_sources))  # noqa: E731
    after_commit(work, "on_report_ready")
