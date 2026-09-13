# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import io
from unittest import mock

import pytest
from django.core.management import call_command
from django.db import IntegrityError, OperationalError, connection
from django.test.utils import CaptureQueriesContext

from django_leads import settings as leads_settings
from django_leads.enums import ActivityKind, ImportStatus
from django_leads.models import Activity, Company, Contact, ImportBatch
from django_leads.services import company_service, import_service

HEADER = (
    "company_name,domain,website,company_type,industry,first_name,last_name,email,job_title,language,legal_basis,phone"
)
CSV = f"""{HEADER}
Lesna,lesna-shop.pl,,RETAILER,garden,Jan,Nowak,jan@lesna-shop.pl,Owner,pl,legitimate_interest,
Lesna,www.lesna-shop.pl/pl,,,,,,,,,,
Ogrod,ogrod.pl,,,,Ewa,Lis,EWA@ogrod.pl,Buyer,pl,,
My Ogrod,,https://sklep.myogrod.pl/x?y,,,,,,,,,
Ogrod,ogrod.pl,,,,,,  ewa@OGROD.pl ,CEO,,consent,123
Nobody,,,,,,,,,,,
,,,,,Anna,Free,anna@gmail.com,,,,
Bad,bad-basis.pl,,,,,,x@bad-basis.pl,,,maybe,
"""


def run(channel, content: str = CSV) -> ImportBatch:
    batch = import_service.create_batch(channel, "leads.csv", len(content.encode()), "test")
    return import_service.run_content(batch, content)


def csv_of(*rows: str) -> str:
    return "\n".join([HEADER, *rows])


def reasons(batch: ImportBatch) -> dict[int, str]:
    return {entry["row"]: entry["reason"] for entry in batch.report}


def counts(batch: ImportBatch) -> tuple[int, int, int]:
    return batch.created_count, batch.matched_count, batch.skipped_count


def test_import_report_counts_and_dedup(channel):
    batch = run(channel)
    assert batch.status == ImportStatus.DONE and counts(batch) == (3, 2, 3)
    assert (batch.row_count, batch.last_row_done, batch.size_bytes) == (8, 9, len(CSV.encode()))
    assert sorted(Company.objects.values_list("domain", flat=True)) == ["lesna-shop.pl", "myogrod.pl", "ogrod.pl"]
    ewa = Contact.objects.get(email="ewa@ogrod.pl")
    assert (ewa.job_title, ewa.legal_basis, ewa.phone, ewa.language.iso2) == ("Buyer", "consent", "123", "pl")
    assert Activity.objects.filter(actor=f"import:{batch.pk}", kind=ActivityKind.IMPORT).count() == 5


def test_L01_csv_import_reports_created_and_matched(channel):
    batch = run(channel, csv_of("Lesna,lesna-shop.pl,,,,,,,,,,", "Lesna,www.lesna-shop.pl/pl,,,garden,,,,,,,"))
    assert counts(batch) == (1, 1, 0) and batch.report == []
    company = Company.objects.get()
    assert (company.domain, company.industry) == ("lesna-shop.pl", "garden")
    messages = Activity.objects.filter(kind=ActivityKind.IMPORT).order_by("id").values_list("message", flat=True)
    assert list(messages) == ["import created", "import matched"]


def test_L04_row_without_domain_and_email_skipped_with_reason(channel):
    batch = run(channel)
    assert reasons(batch) == {7: "no_domain_no_email", 8: "freemail_no_domain", 9: "invalid_legal_basis"}


def test_reimport_is_idempotent(channel):
    run(channel)
    again = run(channel)
    assert counts(again) == (0, 5, 3)
    assert Company.objects.count() == 3 and Contact.objects.count() == 2
    assert not Activity.objects.filter(actor=f"import:{again.pk}").exclude(kind=ActivityKind.IMPORT).exists()


def test_missing_header_fails_the_batch(channel):
    batch = run(channel, "")
    assert batch.status == ImportStatus.FAILED and batch.report == [{"row": 0, "reason": "missing_header"}]


def test_L02_overlong_values_skip_row_not_batch(channel):
    batch = run(
        channel,
        csv_of(
            f"A,a-shop.pl,,,,,,a@a-shop.pl,,,,{'1' * 33}",
            f"B,b-shop.pl,,,,,,b@b-shop.pl,{'x' * 129},,,",
            f"{'C' * 256},c-shop.pl,,,,,,,,,,",
            "D,d-shop.pl,,,,,,d@d-shop.pl,,,,",
        ),
    )
    assert batch.status == ImportStatus.DONE and counts(batch) == (1, 0, 3)
    assert reasons(batch) == {2: "too_long:phone", 3: "too_long:job_title", 4: "too_long:name"}
    assert list(Company.objects.values_list("domain", flat=True)) == ["d-shop.pl"]


def test_csv_error_fails_batch_and_stops(channel, monkeypatch):
    monkeypatch.setattr(leads_settings, "LEADS_IMPORT_CHUNK_SIZE", 1)
    batch = run(
        channel, csv_of("A,a-shop.pl,,,,,,,,,,", f'B,b-shop.pl,,,"{"x" * 140_000}",,,,,,,', "C,c-shop.pl,,,,,,,,,,")
    )
    assert batch.status == ImportStatus.FAILED and counts(batch) == (1, 0, 0)
    assert batch.report == [{"row": 0, "reason": "csv_error"}] and batch.last_row_done == 2
    assert list(Company.objects.values_list("domain", flat=True)) == ["a-shop.pl"]


def test_unexpected_error_fails_batch_without_raising(channel):
    with mock.patch.object(import_service, "build_lookups", side_effect=RuntimeError("boom")):
        batch = run(channel)
    assert batch.status == ImportStatus.FAILED and batch.report == [{"row": 0, "reason": "internal_error"}]


def test_retry_resumes_without_replaying_rows(channel, monkeypatch):
    monkeypatch.setattr(leads_settings, "LEADS_IMPORT_CHUNK_SIZE", 2)
    original, calls = import_service.apply_chunk, []

    def flaky(batch, rows, lookups):
        calls.append(rows[0][0])
        if len(calls) == 2:
            raise OperationalError("connection lost")
        original(batch, rows, lookups)

    batch = import_service.create_batch(channel, "leads.csv", 0, "test")
    with mock.patch.object(import_service, "apply_chunk", flaky), pytest.raises(OperationalError):
        import_service.run_content(batch, CSV)
    batch.refresh_from_db()
    assert (batch.status, batch.last_row_done) == (ImportStatus.RUNNING, 3)
    batch = import_service.run_content(batch, CSV)
    assert batch.status == ImportStatus.DONE and counts(batch) == (3, 2, 3)
    assert Activity.objects.filter(kind=ActivityKind.IMPORT).count() == 5


def test_import_does_not_retain_csv_content(channel, monkeypatch, tmp_path):
    monkeypatch.setattr(leads_settings, "LEADS_IMPORT_TMP_DIR", str(tmp_path))
    seen = []
    original = import_service.run_file
    monkeypatch.setattr(
        import_service, "run_file", lambda batch, path: seen.append(path.parent) or original(batch, path)
    )
    done, failed = run(channel), run(channel, "not,a,header\n1,2,3")
    assert (done.status, failed.status) == (ImportStatus.DONE, ImportStatus.FAILED)
    assert seen == [tmp_path, tmp_path] and list(tmp_path.iterdir()) == []
    assert "content" not in {field.name for field in ImportBatch._meta.get_fields()}


def test_report_capped_and_has_no_row_values(channel, monkeypatch):
    monkeypatch.setattr(leads_settings, "LEADS_IMPORT_REPORT_MAX", 2)
    batch = run(channel, csv_of(*[f"Secret Name {n},,,,,,,,,,," for n in range(5)]))
    assert batch.skipped_count == 5 and len(batch.report) == 2
    assert all(set(entry) == {"row", "reason"} for entry in batch.report)
    assert "Secret" not in str(batch.report)


def test_row_with_name_without_email_creates_contact(channel):
    content = csv_of("Firma,firma.pl,,,,Jan,Bez,,,,,")
    batch = run(channel, content)
    assert counts(batch) == (1, 0, 0)
    assert list(Contact.objects.values_list("first_name", "email")) == [("Jan", "")]
    assert counts(run(channel, content)) == (0, 1, 0) and Contact.objects.count() == 1


def test_invalid_email_skipped_even_with_domain(channel):
    batch = run(channel, csv_of("X,x-shop.pl,,,,,,not-an-email,,,,"))
    assert reasons(batch) == {2: "invalid_email"} and not Company.objects.exists()


def test_L04_import_does_not_overwrite_existing_basis(channel):
    run(channel, csv_of("A,a-shop.pl,,,,,,jan@a-shop.pl,,,legitimate_interest,"))
    batch = run(channel, csv_of("A,a-shop.pl,,,,,,jan@a-shop.pl,,,consent,"))
    assert Contact.objects.get().legal_basis == "legitimate_interest"
    conflict = Activity.objects.get(actor=f"import:{batch.pk}", kind=ActivityKind.LEGAL_BASIS_CONFLICT)
    assert conflict.data == {
        "from": "legitimate_interest",
        "to": "consent",
        "source": "import",
        "consent_ref": f"import:{batch.pk}:2",
    }
    assert not Activity.objects.filter(actor=f"import:{batch.pk}", kind=ActivityKind.LEGAL_BASIS).exists()


def test_L04_basis_change_writes_activity_with_ref(channel):
    run(channel, csv_of("A,a-shop.pl,,,,,,jan@a-shop.pl,,,,"))
    batch = run(channel, csv_of("A,a-shop.pl,,,,,,jan@a-shop.pl,,,consent,"))
    contact = Contact.objects.get()
    assert contact.legal_basis == "consent"
    activity = Activity.objects.get(kind=ActivityKind.LEGAL_BASIS)
    assert activity.contact == contact
    assert activity.data == {"from": None, "to": "consent", "source": "import", "consent_ref": f"import:{batch.pk}:2"}


def test_rejected_bulk_write_redoes_rows_alone(channel, monkeypatch):
    monkeypatch.setattr(import_service._ChunkWriter, "flush", mock.Mock(side_effect=IntegrityError("race")))
    batch = run(channel)
    assert batch.status == ImportStatus.DONE and counts(batch) == (3, 2, 3)
    assert Contact.objects.get(email="ewa@ogrod.pl").legal_basis == "consent"
    assert Activity.objects.filter(kind=ActivityKind.IMPORT).count() == 5


def test_rejected_row_is_skipped_as_conflict(channel, monkeypatch):
    monkeypatch.setattr(import_service._ChunkWriter, "flush", mock.Mock(side_effect=IntegrityError("race")))
    real = company_service.upsert_company

    def upsert(channel, row):
        if row["domain"] == "myogrod.pl":
            raise IntegrityError("race")
        return real(channel, row)

    with mock.patch.object(company_service, "upsert_company", upsert):
        batch = run(channel)
    assert batch.status == ImportStatus.DONE and reasons(batch)[5] == "conflict"


def big_csv(rows: int) -> str:
    lines = [f"Shop {n},shop-{n % 250}.pl,,,,First,Last,p{n}@shop-{n % 250}.pl,,pl,consent," for n in range(rows)]
    return "\n".join([HEADER, *lines])


def chunk_queries(channel, rows: list[tuple[int, dict]]) -> list[str]:
    batch = import_service.create_batch(channel, "big.csv", 0, "test")
    lookups = import_service.build_lookups(channel)
    with CaptureQueriesContext(connection) as context:
        import_service.apply_chunk(batch, rows, lookups)
    sql = [query["sql"] for query in context.captured_queries]
    return [query for query in sql if "SAVEPOINT" not in query.upper()]


def test_L05_5000_rows_chunked_query_ceiling(channel):
    """Every chunk: 2 selects + bulk writes, whatever its size (sqlite splits bulk inserts, so the
    exact ceiling is asserted on PostgreSQL — zeno `make module-test`). Savepoints are not counted."""
    rows = list(enumerate(import_service.parse_rows(io.StringIO(big_csv(5000))), start=2))
    for start in range(0, 5000, 500):
        sql = chunk_queries(channel, rows[start : start + 500])
        assert sum(query.lstrip().upper().startswith("SELECT") for query in sql) <= 2
        if connection.vendor == "postgresql":
            assert len(sql) <= 9, sql
    assert Company.objects.count() == 250 and Contact.objects.count() == 5000


def test_command_imports_in_chunks(channel, monkeypatch, tmp_path):
    monkeypatch.setattr(leads_settings, "LEADS_IMPORT_CHUNK_SIZE", 2)
    progress = []
    original = import_service.save_progress

    def save_progress(batch, outcomes, last_line) -> None:
        original(batch, outcomes, last_line)
        progress.append(ImportBatch.objects.get(pk=batch.pk).last_row_done)

    monkeypatch.setattr(import_service, "save_progress", save_progress)
    path = tmp_path / "leads.csv"
    path.write_text(CSV, encoding="utf-8")
    call_command("leads_import_csv", "--channel", channel.idx, "--file", str(path), "--sync", stdout=io.StringIO())
    batch = ImportBatch.objects.get()
    assert batch.status == ImportStatus.DONE and batch.skipped_count == 3
    assert progress == [3, 5, 7, 9] and path.exists()
