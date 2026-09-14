# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""CSV file connector — the source of `leads_import_csv`, `POST imports/` and the import task."""

import csv
from collections.abc import Iterable, Iterator
from dataclasses import fields
from pathlib import Path

from django_leads.connectors.base import CandidateRow, ImportFailed
from django_leads.models import Channel, Company

CSV_COLUMNS = tuple(field.name for field in fields(CandidateRow) if field.name != "external_ref")


def parse_rows(file: Iterable[str]) -> Iterator[dict[str, str]]:
    """Stream CSV rows as dicts of the known columns; the header row is required."""
    reader = csv.DictReader(file)
    if not reader.fieldnames or not set(CSV_COLUMNS) & {name.strip() for name in reader.fieldnames}:
        raise ImportFailed("missing_header")
    for raw in reader:
        yield {column: (raw.get(column) or "").strip() for column in CSV_COLUMNS}


class CsvConnector:
    key = "csv"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch_candidates(self, channel: Channel) -> Iterator[CandidateRow]:
        """Streams the file (UTF-8, header row); the channel does not change what a file holds."""
        with self.path.open(encoding="utf-8", newline="") as file:
            for row in parse_rows(file):
                yield CandidateRow(**row)

    def push_status(self, company: Company) -> None:
        """A file has nothing to update."""
