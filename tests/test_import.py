# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import io

from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from django_leads import settings as leads_settings
from django_leads.enums import ImportStatus
from django_leads.models import Activity, Company, Contact, ImportBatch
from django_leads.services import import_service

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
    return import_service.run_batch(import_service.create_batch(channel, "leads.csv", content, "test"))


def reasons(batch: ImportBatch) -> dict[int, str]:
    return {entry["row"]: entry["reason"] or entry["action"] for entry in batch.report}


def test_import_report_counts_and_dedup(channel):
    batch = run(channel)
    assert batch.status == ImportStatus.DONE
    assert (batch.created_count, batch.matched_count, batch.skipped_count) == (3, 2, 3)
    assert reasons(batch)[3] == "matched" and reasons(batch)[6] == "matched"
    assert sorted(Company.objects.values_list("domain", flat=True)) == ["lesna-shop.pl", "myogrod.pl", "ogrod.pl"]
    ewa = Contact.objects.get(email="ewa@ogrod.pl")
    assert (ewa.job_title, ewa.legal_basis, ewa.phone, ewa.language.iso2) == ("Buyer", "consent", "123", "pl")
    assert Activity.objects.filter(actor=f"import:{batch.pk}").count() == 5


def test_L04_row_without_domain_and_email_skipped_with_reason(channel):
    batch = run(channel)
    assert reasons(batch)[7] == "no_domain_no_email"
    assert reasons(batch)[8] == "freemail_no_domain"
    assert reasons(batch)[9] == "invalid_legal_basis"
    assert len(batch.report) == 8


def test_reimport_is_idempotent(channel):
    run(channel)
    again = run(channel)
    assert (again.created_count, again.matched_count) == (0, 5)
    assert Company.objects.count() == 3 and Contact.objects.count() == 2


def test_missing_header_fails_the_batch(channel):
    batch = run(channel, "")
    assert batch.status == ImportStatus.FAILED and batch.report[0]["reason"] == "missing CSV header"


def big_csv(rows: int) -> str:
    lines = [f"Shop {n},shop-{n % 250}.pl,,,,First,Last,p{n}@shop-{n % 250}.pl,,pl,consent," for n in range(rows)]
    return "\n".join([HEADER, *lines])


def chunk_queries(channel, rows: list[tuple[int, dict]]) -> list[str]:
    batch = import_service.create_batch(channel, "big.csv", "", "test")
    lookups = import_service.build_lookups(channel)
    with CaptureQueriesContext(connection) as context:
        import_service.apply_chunk(batch, rows, lookups)
    return [query["sql"] for query in context.captured_queries]


def test_L05_5000_rows_chunked_query_ceiling(channel):
    """Every chunk: 2 selects + bulk writes, whatever its size (sqlite splits bulk inserts, so the
    exact ceiling is asserted on PostgreSQL — zeno `make module-test`)."""
    rows = list(enumerate(import_service.parse_rows(io.StringIO(big_csv(5000))), start=2))
    for start in range(0, 5000, 500):
        sql = chunk_queries(channel, rows[start : start + 500])
        assert sum(query.lstrip().upper().startswith("SELECT") for query in sql) <= 2
        if connection.vendor == "postgresql":
            assert len(sql) <= 9, sql
    assert Company.objects.count() == 250 and Contact.objects.count() == 5000


def test_command_imports_in_chunks(channel, monkeypatch, tmp_path):
    monkeypatch.setattr(leads_settings, "LEADS_IMPORT_CHUNK_SIZE", 2)
    saves = []
    monkeypatch.setattr(import_service._ChunkWriter, "flush", record_flush(saves))
    path = tmp_path / "leads.csv"
    path.write_text(CSV, encoding="utf-8")
    call_command("leads_import_csv", "--channel", channel.idx, "--file", str(path), "--sync", stdout=io.StringIO())
    batch = ImportBatch.objects.get()
    assert batch.status == ImportStatus.DONE and len(batch.report) == 8
    assert saves == [2, 4, 6, 8]


def record_flush(saves: list[int]):
    original = import_service._ChunkWriter.flush

    def flush(writer) -> None:
        original(writer)
        saves.append(len(ImportBatch.objects.get(pk=writer.batch.pk).report))

    return flush
