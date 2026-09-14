# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from django_leads.services import gdpr_service


class Command(BaseCommand):
    help = "GDPR export (art. 15) or erasure (art. 17) of everything held about an email, across modules."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--email", required=True, help="Address of the data subject")
        action = parser.add_mutually_exclusive_group(required=True)
        action.add_argument("--export", metavar="PATH", help="Write the JSON export to this file (mode 0600)")
        action.add_argument("--erase", action="store_true", help="Pseudonymise the address everywhere (irreversible)")
        parser.add_argument("--yes", action="store_true", help="Erase without the interactive confirmation")

    def handle(self, *args, **options) -> None:
        email = options["email"]
        if options["export"]:
            self._export(email, Path(options["export"]))
            return
        if not options["yes"] and input(f"Erase all data of {email}? This cannot be undone [y/N] ").lower() != "y":
            raise CommandError("erasure aborted")
        for module, counts in gdpr_service.erase(email, actor="manage.py").items():
            self.stdout.write(f"{module}: {json.dumps(counts, sort_keys=True)}")

    def _export(self, email: str, path: Path) -> None:
        payload = gdpr_service.export(email)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with open(descriptor, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        self.stdout.write(f"modules: {', '.join(payload['modules'])}")
        self.stdout.write(f"written to {path}")
