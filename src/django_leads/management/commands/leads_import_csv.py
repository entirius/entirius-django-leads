# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from django_leads.models import Channel
from django_leads.services import import_service
from django_leads.tasks import import_csv


class Command(BaseCommand):
    help = "Import companies and contacts from a CSV file into a leads channel."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--channel", required=True, help="Leads channel idx")
        parser.add_argument("--file", required=True, help="Path to the CSV file (header row required)")
        parser.add_argument("--sync", action="store_true", help="Run in this process instead of the Celery queue")

    def handle(self, *args, **options) -> None:
        channel = Channel.objects.filter(idx=options["channel"]).first()
        if channel is None:
            raise CommandError(f"leads channel {options['channel']!r} not found")
        path = Path(options["file"])
        batch = import_service.create_batch(channel, path.name, path.read_text(encoding="utf-8-sig"), "manage.py")
        if not options["sync"]:
            import_csv.delay(batch.pk)
            self.stdout.write(f"batch {batch.pk} queued")
            return
        batch = import_service.run_batch(batch)
        counts = f"created={batch.created_count} matched={batch.matched_count} skipped={batch.skipped_count}"
        self.stdout.write(f"batch {batch.pk} {batch.status}: {counts}")
