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

CSV_COLUMNS = tuple(
    field.name
    for field in fields(CandidateRow)
    if field.name not in ("external_ref", "attributes", "contact_attributes")
)
# `attr.<key>` → company attribute, `contact.attr.<key>` → contact attribute (`CandidateRow` dict fields).
ATTRIBUTE_PREFIXES = {"attr.": "attributes", "contact.attr.": "contact_attributes"}
# Older files name a column the way it used to be called; read when the current name is absent from the header.
COLUMN_ALIASES = {"lead_type": "company_type"}


def parse_rows(file: Iterable[str]) -> Iterator[dict[str, str]]:
    """Stream CSV rows as dicts of the known columns; the header row is required. Header names are matched
    stripped of surrounding whitespace, and values are read through that same stripped-to-raw mapping so a
    header like `" domain"` reads its own column instead of an empty one."""
    reader = csv.DictReader(file)
    raw_names = {name.strip(): name for name in reader.fieldnames or ()}
    for column, old in COLUMN_ALIASES.items():
        if column not in raw_names and old in raw_names:
            raw_names[column] = raw_names[old]
    if not set(CSV_COLUMNS) & set(raw_names):
        raise ImportFailed("missing_header")
    for raw in reader:
        row = {column: (raw.get(raw_names.get(column, column)) or "").strip() for column in CSV_COLUMNS}
        yield {**row, **_attribute_cells(raw, raw_names)}


def _attribute_cells(raw: dict[str, str], raw_names: dict[str, str]) -> dict[str, dict[str, str]]:
    cells: dict[str, dict[str, str]] = {target: {} for target in ATTRIBUTE_PREFIXES.values()}
    for name, raw_name in raw_names.items():
        prefix = next((prefix for prefix in ATTRIBUTE_PREFIXES if name.startswith(prefix)), None)
        if prefix is not None:
            cells[ATTRIBUTE_PREFIXES[prefix]][name.removeprefix(prefix)] = (raw.get(raw_name) or "").strip()
    return cells


def read_header(file: Iterable[str]) -> list[str]:
    """The header names, stripped — what the import reports as ignored columns."""
    return [name.strip() for name in next(csv.reader(file), [])]


class CsvConnector:
    key = "csv"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch_candidates(self, channel: Channel) -> Iterator[CandidateRow]:
        """Streams the file (UTF-8, header row); the channel does not change what a file holds."""
        with self.path.open(encoding="utf-8", newline="") as file:
            for row in parse_rows(file):
                yield CandidateRow(**row)

    def header(self) -> list[str]:
        with self.path.open(encoding="utf-8", newline="") as file:
            return read_header(file)

    def push_status(self, company: Company) -> None:
        """A file has nothing to update."""
