# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from django.apps import AppConfig

AGENT_AREA = "ai_crm.agent"


class DjangoAiCrmConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "django_ai_crm"
    label = "django_ai_crm"
    verbose_name = "AI CRM"
    is_volkanos = True
    # The agent reads the caller's day and writes through the other modules' areas: every tool also asks access for
    # the area of what it touches (todo.tasks, leads.companies, communicator.review), so this area alone never writes.
    access_areas = [
        {"key": AGENT_AREA, "label": "AI CRM agent (day plan, chat, MCP)", "sensitive": ("pii", "ai_cost")},
    ]
    # Every admin view carries its access_area; no route needs a path rule.
    access_route_rules = []
