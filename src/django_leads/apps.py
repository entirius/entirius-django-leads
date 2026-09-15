# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from django.apps import AppConfig


class DjangoLeadsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "django_leads"
    label = "django_leads"
    is_volkanos = True

    def ready(self) -> None:
        from django_leads.signals import (
            communicator_receivers,
            contact_forms_bridge,
            leads_receivers,
            siteintel_receivers,
        )

        contact_forms_bridge.connect()
        leads_receivers.connect()
        siteintel_receivers.connect()
        communicator_receivers.connect()
