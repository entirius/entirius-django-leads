# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""An uploaded import file read into a table of strings: CSV (`,` `;` tab `|`; UTF-8, UTF-8/UTF-16 with BOM,
cp1250) or `.xlsx` (the first sheet). The first non-empty row is the header; empty rows are dropped, so row numbers
count the header as 1 and the non-empty data rows after it. Nothing here touches the database."""

import csv
import io
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, time

ZIP_MAGIC = b"PK\x03\x04"
OLE_MAGIC = b"\xd0\xcf\x11\xe0"  # .xls, .doc — the old binary Office formats
DELIMITERS = (",", ";", "\t", "|")
SNIFF_LINES = 20


class ImportFileError(ValueError):
    """The file cannot be read; `code` is the API issue (`unsupported_format`, `encoding`, `unreadable`,
    `no_header`)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Table:
    format: str
    columns: list[str]
    rows: list[list[str]]
    encoding: str | None = None
    delimiter: str | None = None
    sheet: str | None = None
    raw_header: list[str] = field(default_factory=list)

    def column_values(self, index: int) -> list[str]:
        return [row[index] for row in self.rows]


def read_table(filename: str, data: bytes) -> Table:
    """The table of an upload; raises `ImportFileError`."""
    if data.startswith(ZIP_MAGIC):
        return _read_xlsx(data)
    if data.startswith(OLE_MAGIC) or data.startswith(b"%PDF"):
        raise ImportFileError("unsupported_format", "Only .csv and .xlsx files can be imported.")
    text, encoding = decode(data)
    if "\x00" in text[:4096]:
        raise ImportFileError("unsupported_format", "Only .csv and .xlsx files can be imported.")
    return _read_csv(text, encoding)


def decode(data: bytes) -> tuple[str, str]:
    """UTF-8 (with or without BOM), UTF-16 with BOM, else cp1250 — the code page of Polish Excel exports."""
    if data.startswith(b"\xef\xbb\xbf"):
        candidates = ("utf-8-sig",)
    elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
        candidates = ("utf-16",)
    else:
        candidates = ("utf-8", "cp1250")
    for encoding in candidates:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ImportFileError("encoding", "The file encoding is not UTF-8, UTF-16 or Windows-1250.")


def sniff_delimiter(text: str) -> str:
    """The candidate that splits the first lines into the most columns, the same number on most lines."""
    lines = [line for line in text.splitlines()[:SNIFF_LINES] if line.strip()]
    if not lines:
        return ","
    best, best_score = ",", 0
    for delimiter in DELIMITERS:
        try:
            counts = [len(row) for row in csv.reader(lines, delimiter=delimiter)]
        except csv.Error:
            continue
        header = counts[0]
        steady = sum(1 for count in counts if count == header)
        score = header * steady if header > 1 else 0
        if score > best_score:
            best, best_score = delimiter, score
    return best


def _read_csv(text: str, encoding: str) -> Table:
    delimiter = sniff_delimiter(text)
    try:
        rows = [
            [cell.strip() for cell in row] for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
        ]
    except csv.Error:
        raise ImportFileError("unreadable", "The CSV file cannot be read.") from None
    return _table("csv", rows, encoding=encoding, delimiter=delimiter)


def _read_xlsx(data: bytes) -> Table:
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, OSError, ValueError, TypeError):
        raise ImportFileError("unreadable", "The .xlsx file cannot be read.") from None
    try:
        sheet = workbook.worksheets[0] if workbook.worksheets else None
        if sheet is None:
            raise ImportFileError("no_header", "The file has no header row.")
        rows = [[cell_text(value) for value in row] for row in sheet.iter_rows(values_only=True)]
        return _table("xlsx", rows, sheet=sheet.title)
    finally:
        workbook.close()


def cell_text(value: object) -> str:
    """A spreadsheet cell as the text a CSV export would show: `48123456789.0` → `48123456789`, dates ISO."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time(0) else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def _table(kind: str, rows: list[list[str]], **meta: str) -> Table:
    filled = [row for row in rows if any(cell for cell in row)]
    if not filled:
        raise ImportFileError("no_header", "The file has no header row.")
    raw_header, data_rows = filled[0], filled[1:]
    width = _used_width(filled)
    raw_header = (raw_header + [""] * width)[:width]
    data_rows = [(row + [""] * width)[:width] for row in data_rows]
    return Table(kind, unique_names(raw_header), data_rows, raw_header=raw_header, **meta)


def _used_width(rows: list[list[str]]) -> int:
    """Trailing columns empty in every row (Excel's formatted-but-empty cells) are not columns."""
    width = 0
    for row in rows:
        used = max((index + 1 for index, cell in enumerate(row) if cell), default=0)
        width = max(width, used)
    return width


def unique_names(header: list[str]) -> list[str]:
    """Header names as mapping keys: an empty one is `Column <n>` (from 1), a repeated one `<name> (2)`."""
    names: list[str] = []
    seen: dict[str, int] = {}
    for position, raw in enumerate(header, start=1):
        name = raw.strip() or f"Column {position}"
        count = seen.get(name.casefold(), 0) + 1
        seen[name.casefold()] = count
        names.append(name if count == 1 else f"{name} ({count})")
    return names
