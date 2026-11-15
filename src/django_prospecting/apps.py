# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from django.apps import AppConfig

PEOPLE_AREA = "prospecting.people"
COMPANIES_AREA = "prospecting.companies"


class DjangoProspectingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "django_prospecting"
    label = "django_prospecting"
    verbose_name = "Prospecting"
    is_volkanos = True
    # People holds names, job titles and guessed addresses of real persons.
    access_areas = [
        {"key": PEOPLE_AREA, "label": "Prospecting: people to contact", "sensitive": ("pii",)},
        {"key": COMPANIES_AREA, "label": "Prospecting: new companies"},
    ]
    # Every admin view carries its access_area; no route needs a path rule.
    access_route_rules = []

    def ready(self) -> None:
        from django_prospecting.signals import leads_receivers

        leads_receivers.connect()
