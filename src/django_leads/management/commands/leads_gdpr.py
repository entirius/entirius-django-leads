# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import json
import os
import sys
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email

from django_leads.services import gdpr_service
from django_leads.utils.emails import normalize_email


class Command(BaseCommand):
    help = "GDPR export (art. 15) or erasure (art. 17) of everything held about an email, across modules."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--email", required=True, help="Address of the data subject")
        action = parser.add_mutually_exclusive_group(required=True)
        action.add_argument("--export", metavar="PATH", help="Write the JSON export to this file (mode 0600)")
        action.add_argument("--erase", action="store_true", help="Pseudonymise the address everywhere (irreversible)")
        parser.add_argument("--yes", action="store_true", help="Erase without the interactive confirmation")

    def handle(self, *args, **options) -> None:
        email = valid_email(options["email"])
        if options["export"]:
            self._export(email, Path(options["export"]))
            return
        if not options["yes"]:
            confirm(email)
        for module, counts in gdpr_service.erase(email, actor="manage.py").items():
            self.stdout.write(f"{module}: {json.dumps(counts, sort_keys=True)}")

    def _export(self, email: str, path: Path) -> None:
        payload = gdpr_service.export(email)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with open(descriptor, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        self.stdout.write(f"modules: {', '.join(payload['modules'])}")
        self.stdout.write(f"written to {path}")


def valid_email(raw: str) -> str:
    """Normalised and validated — a blank or malformed value never selects anyone."""
    email = normalize_email(raw)
    try:
        validate_email(email)
    except ValidationError:
        raise CommandError("--email must be a valid email address") from None
    return email


def confirm(email: str) -> None:
    """Asks on a terminal only; without one (cron, pipes, CI) the erasure needs `--yes`."""
    if not sys.stdin.isatty():
        raise CommandError("no terminal to confirm the erasure on — pass --yes")
    try:
        answer = input(f"Erase all data of {email}? This cannot be undone [y/N] ")
    except EOFError:
        raise CommandError("erasure aborted: no answer") from None
    if answer.strip().lower() != "y":
        raise CommandError("erasure aborted")
