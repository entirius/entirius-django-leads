# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""U17: an import file whose headers are not the exact column names — the proposal, the preview, the mapped import,
xlsx / cp1250 / `;` files, conflicts, custom fields, the optional AI step, and the unchanged exact-name import."""

import io
import json
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook

from django_leads import settings as leads_settings
from django_leads.enums import ImportStatus
from django_leads.models import Company, Contact, ImportBatch
from django_leads.services import attribute_service, import_mapping_service
from django_leads.services.import_file_service import ImportFileError, read_table, sniff_delimiter
from tests.conftest import api_url


def upload(content: bytes | str, name: str = "baza.csv") -> SimpleUploadedFile:
    data = content.encode() if isinstance(content, str) else content
    return SimpleUploadedFile(name, data, content_type="application/octet-stream")


def preview(admin_api, content, name="baza.csv", mapping=None):
    body = {"file": upload(content, name)}
    if mapping is not None:
        body["mapping"] = mapping if isinstance(mapping, str) else json.dumps(mapping)
    return admin_api.post(api_url("imports/preview/"), body, format="multipart")


def import_now(admin_api, content, name="baza.csv", mapping="auto", **extra):
    body = {"file": upload(content, name), **extra}
    if mapping is not None:
        body["mapping"] = mapping if isinstance(mapping, str) else json.dumps(mapping)
    return admin_api.post(api_url("test/import-now/"), body, format="multipart")


def columns_of(response) -> dict[str, dict]:
    assert response.status_code == 200, response.content
    return {column["name"]: column for column in response.json()["columns"]}


def xlsx_bytes(rows: list[list]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Arkusz1"
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def issue(response) -> tuple[str, str]:
    assert response.status_code == 400, response.content
    detail = response.json()["details"][0]
    return detail["field"], detail["issue"]


# --- the header words ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "field"),
    [
        ("name", "company_name"),
        ("Nazwa firmy", "company_name"),
        ("FIRMA", "company_name"),
        ("Company Name", "company_name"),
        ("Organisation", "company_name"),
        ("Kontrahent", "company_name"),
        ("Unternehmen", "company_name"),
        ("WWW", "website"),
        ("Strona internetowa", "website"),
        ("Website", "website"),
        ("Domena", "domain"),
        ("E-mail", "email"),
        ("Adres e-mail", "email"),
        ("e_mail", "email"),
        ("Emails", "email"),
        ("Telefon", "phone"),
        ("Tel.", "phone"),
        ("Komórka", "phone"),
        ("Mobile", "phone"),
        ("Numer telefonu", "phone"),
        ("Imię", "first_name"),
        ("Vorname", "first_name"),
        ("Nazwisko", "last_name"),
        ("Imię i nazwisko", "full_name"),
        ("Osoba kontaktowa", "full_name"),
        ("Stanowisko", "job_title"),
        ("Job title", "job_title"),
        ("Branża", "industry"),
        ("Uwagi", "description"),
        ("Notes", "description"),
        ("Język", "language"),
        ("company_name", "company_name"),
        ("company_type", "lead_type"),
        ("Telefon komórkowy służbowy", "phone"),
        ("E-mail osoby kontaktowej", "email"),
    ],
)
def test_header_words_pl_en_de(admin_api, channel, header, field):
    column = columns_of(preview(admin_api, f"{header},x\nvalue,1\n"))[header]
    assert column["field"] == field
    assert column["source"] in ("header", "exact")


def test_exact_and_synonym_are_certain_a_word_inside_a_longer_header_is_to_check(admin_api, channel):
    columns = columns_of(preview(admin_api, "company_name,Telefon,Telefon komórkowy służbowy\nA,1,2\n"))
    assert (columns["company_name"]["confidence"], columns["company_name"]["level"]) == (1.0, "certain")
    assert (columns["Telefon"]["level"], columns["Telefon"]["source"]) == ("certain", "header")
    assert columns["Telefon komórkowy służbowy"]["field"] == "ignore"  # phone already taken by the better column
    assert columns["Telefon komórkowy służbowy"]["reason"] == "duplicate_field"


@pytest.mark.parametrize(
    ("header", "concept"),
    [("NIP", "tax_id"), ("VAT ID", "tax_id"), ("REGON", "regon"), ("KRS", "krs"), ("Miasto", "city"),
     ("Ulica", "street"), ("Kod pocztowy", "postal_code"), ("Kraj", "country"), ("Liczba pracowników", "employees")],
)  # fmt: skip
def test_concept_without_a_crm_field_is_ignored_with_the_concept(admin_api, channel, header, concept):
    column = columns_of(preview(admin_api, f"{header},domain\nx,a.pl\n"))[header]
    assert (column["field"], column["reason"], column["concept"]) == ("ignore", "no_crm_field", concept)
    assert column["level"] == "unrecognised"


# --- the values ------------------------------------------------------------------------------------------------------


def test_values_name_a_column_when_its_header_says_nothing(admin_api, channel):
    content = (
        "A,B,C,D,E,F\n"
        "jan@alfa.pl,alfa.pl,https://www.alfa.pl/,+48 600 100 200,526-025-02-74,00-950\n"
        "ola@beta.pl,beta.pl,www.beta.pl,600200300,PL5260250274,31-001\n"
    )
    columns = columns_of(preview(admin_api, content))
    assert {name: column["field"] for name, column in columns.items()} == {
        "A": "email",
        "B": "domain",
        "C": "website",
        "D": "phone",
        "E": "ignore",
        "F": "ignore",
    }
    assert {columns[name]["source"] for name in "ABCD"} == {"content"}
    assert {columns[name]["level"] for name in "ABCD"} == {"check"}
    assert (columns["E"]["concept"], columns["F"]["concept"]) == ("tax_id", "postal_code")


def test_nip_checksum():
    assert import_mapping_service.is_nip("526-025-02-74")
    assert not import_mapping_service.is_nip("5260250275")
    assert import_mapping_service.is_regon("012100784")


def test_strong_values_beat_a_vague_header(admin_api, channel):
    """`Kontakt` reads as a person, but a column of addresses is an email column."""
    columns = columns_of(preview(admin_api, "Kontakt,Firma\njan@alfa.pl,Alfa\nola@beta.pl,Beta\n"))
    assert columns["Kontakt"]["field"] == "email"


def test_empty_column_is_reported_as_empty(admin_api, channel):
    columns = columns_of(preview(admin_api, "domain,qwerty\na.pl,\nb.pl,\n"))
    assert (columns["qwerty"]["field"], columns["qwerty"]["reason"]) == ("ignore", "empty_column")


# --- custom fields ---------------------------------------------------------------------------------------------------


@pytest.fixture
def custom_fields(channel):
    attribute_service.create_definition(channel, {"entity": "company", "key": "nip", "label": "NIP", "kind": "text"})
    attribute_service.create_definition(
        channel, {"entity": "contact", "key": "linkedin", "label": "LinkedIn", "kind": "text"}
    )


def test_custom_fields_by_key_label_and_concept(admin_api, channel, custom_fields):
    columns = columns_of(preview(admin_api, "Numer NIP,Profil LinkedIn,domain\n5260250274,in/jan,a.pl\n"))
    assert (columns["Numer NIP"]["field"], columns["Numer NIP"]["source"]) == ("attr.nip", "custom_field")
    assert columns["Profil LinkedIn"]["field"] == "contact.attr.linkedin"
    fields = admin_api.get(api_url("imports/fields/")).json()["results"]
    assert [item["key"] for item in fields][-2:] == ["attr.nip", "contact.attr.linkedin"]
    assert fields[0] == {"key": "company_name", "label": "Company name", "entity": "company", "custom": False,
                         "kind": "text"}  # fmt: skip


def test_custom_field_values_are_imported(admin_api, channel, custom_fields):
    response = import_now(admin_api, "Firma,NIP,WWW\nAlfa,5260250274,alfa.pl\n")
    assert response.status_code == 200, response.content
    assert Company.objects.get(domain="alfa.pl").attributes == {"nip": "5260250274"}


# --- conflicts -------------------------------------------------------------------------------------------------------


def test_two_columns_for_one_field_the_more_certain_wins(admin_api, channel):
    columns = columns_of(preview(admin_api, "E-mail firmowy,Email,domain\na@a.pl,b@a.pl,a.pl\n"))
    assert columns["Email"]["field"] == "email"
    loser = columns["E-mail firmowy"]
    assert (loser["field"], loser["reason"], loser["conflict_with"], loser["level"]) == (
        "ignore",
        "duplicate_field",
        "Email",
        "check",
    )


def test_full_name_gives_way_to_first_and_last_name(admin_api, channel):
    columns = columns_of(preview(admin_api, "Imię,Nazwisko,Imię i nazwisko,domain\nJan,Kowalski,Jan Kowalski,a.pl\n"))
    assert (columns["Imię"]["field"], columns["Nazwisko"]["field"]) == ("first_name", "last_name")
    assert columns["Imię i nazwisko"]["reason"] == "duplicate_field"


def test_the_users_choice_wins_a_conflict_in_the_preview(admin_api, channel):
    columns = columns_of(
        preview(admin_api, "Email,Inny mail,domain\na@a.pl,b@a.pl,a.pl\n", mapping={"Inny mail": "email"})
    )
    assert (columns["Inny mail"]["field"], columns["Inny mail"]["source"]) == ("email", "user")
    assert columns["Email"]["conflict_with"] == "Inny mail"


# --- the files -------------------------------------------------------------------------------------------------------


POLISH_ROWS = (
    "Nazwa firmy;Strona WWW;E-mail;Imię i nazwisko;Stanowisko;Telefon;Uwagi\n"
    "Łódzka Spółka Ogrodnicza;https://www.lodzka-ogrody.pl;jan.zolc@lodzka-ogrody.pl;Jan Żółć;Prezes;600 100 200;"
    "Klient z targów\n"
    "Gdańskie Żagle;gdanskie-zagle.pl;;Kowalska, Anna;Kierownik zakupów;;\n"
)


def test_cp1250_semicolon_csv_imports_polish_names(admin_api, channel):
    data = POLISH_ROWS.encode("cp1250")
    body = preview(admin_api, data).json()
    assert (body["format"], body["encoding"], body["delimiter"], body["row_count"]) == ("csv", "cp1250", ";", 2)
    assert body["rows"][0]["values"]["first_name"] == "Jan"
    response = import_now(admin_api, data)
    assert response.status_code == 200, response.content
    assert response.json()["status"] == ImportStatus.DONE and response.json()["created_count"] == 2
    lodz = Company.objects.get(domain="lodzka-ogrody.pl")
    assert (lodz.name, lodz.website, lodz.description) == (
        "Łódzka Spółka Ogrodnicza",
        "https://www.lodzka-ogrody.pl",
        "Klient z targów",
    )
    jan = Contact.objects.get(email="jan.zolc@lodzka-ogrody.pl")
    assert (jan.first_name, jan.last_name, jan.job_title, jan.phone) == ("Jan", "Żółć", "Prezes", "600 100 200")
    anna = Contact.objects.get(company__domain="gdanskie-zagle.pl")
    assert (anna.first_name, anna.last_name) == ("Anna", "Kowalska")


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16"])
def test_unicode_encodings(admin_api, channel, encoding):
    body = preview(admin_api, POLISH_ROWS.replace(";", "\t").encode(encoding)).json()
    assert (body["encoding"], body["delimiter"]) == (encoding, "\t")
    assert body["rows"][1]["values"]["company_name"] == "Gdańskie Żagle"


def test_xlsx_first_sheet_numbers_and_blank_rows(admin_api, channel):
    data = xlsx_bytes(
        [
            [None, None, None],
            ["Firma", "Adres e-mail", "Tel."],
            ["Alfa", "jan@alfa.pl", 48600100200],
            [None, None, None],
            ["Beta", "ola@beta.pl", 600200300.0],
        ]
    )
    body = preview(admin_api, data, name="baza.xlsx").json()
    assert (body["format"], body["sheet"], body["row_count"]) == ("xlsx", "Arkusz1", 2)
    assert [column["field"] for column in body["columns"]] == ["company_name", "email", "phone"]
    assert body["rows"][1] == {
        "row": 3,
        "values": {"company_name": "Beta", "email": "ola@beta.pl", "phone": "600200300"},
        "skip_reason": None,
    }
    response = import_now(admin_api, data, name="baza.xlsx")
    assert response.json()["created_count"] == 2
    assert Company.objects.get(domain="alfa.pl").name == "Alfa"
    assert Contact.objects.get(email="jan@alfa.pl").phone == "48600100200"


def test_unique_names_for_empty_and_repeated_headers():
    table = read_table("x.csv", b"Email,,Email\na@a.pl,x,b@a.pl\n")
    assert table.columns == ["Email", "Column 2", "Email (2)"]


def test_delimiter_sniffing():
    assert sniff_delimiter("a;b;c\n1;2;3\n") == ";"
    assert sniff_delimiter("a,b\n1,2\n") == ","
    assert sniff_delimiter("a|b|c\n1|2|3\n") == "|"


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"\xd0\xcf\x11\xe0" + b"\x00" * 20, "unsupported_format"),
        (b"%PDF-1.4", "unsupported_format"),
        (b"", "no_header"),
        (b"\n\n", "no_header"),
        (b"PK\x03\x04broken", "unreadable"),
    ],
)
def test_unreadable_files(data, code):
    with pytest.raises(ImportFileError) as error:
        read_table("x", data)
    assert error.value.code == code


def test_file_errors_are_v2_400s(admin_api, channel):
    assert issue(preview(admin_api, b"\xd0\xcf\x11\xe0" + b"\x00" * 20, name="old.xls")) == (
        "file",
        "UNSUPPORTED_FORMAT",
    )
    assert issue(admin_api.post(api_url("imports/preview/"), {}, format="multipart")) == ("file", "REQUIRED")
    assert issue(import_now(admin_api, b"", mapping="auto")) == ("file", "NO_HEADER")


# --- C9: `name,domain,email` -----------------------------------------------------------------------------------------


C9 = "name,domain,email,phone_extra\nTest Import Alfa,alfa-import.test,,1\nTest Import Beta,beta-import.test,,2\n"


def test_c9_file_gets_company_names(admin_api, channel):
    columns = columns_of(preview(admin_api, C9))
    assert columns["name"]["field"] == "company_name"
    assert columns["phone_extra"]["field"] in ("phone", "ignore")
    response = import_now(admin_api, C9, mapping={"name": "company_name", "domain": "domain", "email": "email"})
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["created_count"] == 2 and body["ignored_columns"] == ["phone_extra"]
    assert body["column_mapping"] == [
        {"column": "name", "field": "company_name", "label": "Company name"},
        {"column": "domain", "field": "domain", "label": "Domain"},
        {"column": "email", "field": "email", "label": "Email"},
        {"column": "phone_extra", "field": "ignore", "label": None},
    ]
    names = set(Company.objects.values_list("name", flat=True))
    assert names == {"Test Import Alfa", "Test Import Beta"}


def test_queued_import_keeps_the_mapping_on_the_batch(admin_api, channel, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        response = admin_api.post(api_url("imports/"), {"file": upload(C9), "mapping": "auto"}, format="multipart")
    assert response.status_code == 202, response.content
    assert response.json()["column_mapping"][0] == {"column": "name", "field": "company_name", "label": "Company name"}
    batch = ImportBatch.objects.get(pk=response.json()["id"])
    detail = admin_api.get(api_url(f"imports/{batch.pk}/")).json()
    assert detail["status"] == "done" and detail["created_count"] == 2
    assert detail["column_mapping"] == response.json()["column_mapping"]
    assert Company.objects.get(domain="alfa-import.test").name == "Test Import Alfa"


# --- the exact-name import stays as it was ---------------------------------------------------------------------------


def test_without_mapping_the_old_import_is_unchanged(admin_api, channel):
    response = import_now(admin_api, C9, mapping=None)
    body = response.json()
    assert body["created_count"] == 2 and body["column_mapping"] == []
    assert body["ignored_columns"] == ["name", "phone_extra"]
    assert Company.objects.get(domain="alfa-import.test").name == "alfa-import.test"


def test_without_mapping_xlsx_and_semicolons_read_exact_names(admin_api, channel):
    data = xlsx_bytes([["company_name", "domain"], ["Alfa", "alfa.pl"]])
    assert import_now(admin_api, data, name="a.xlsx", mapping=None).json()["created_count"] == 1
    assert import_now(admin_api, "company_name;domain\nBeta;beta.pl\n", mapping=None).json()["created_count"] == 1
    assert set(Company.objects.values_list("name", flat=True)) == {"Alfa", "Beta"}


def test_exact_description_column_is_now_read(admin_api, channel):
    body = import_now(admin_api, "company_name,domain,description\nAlfa,alfa.pl,Notatka\n", mapping=None).json()
    assert body["ignored_columns"] == []
    assert Company.objects.get().description == "Notatka"


# --- the mapping the user sends --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mapping", "expected"),
    [
        ("{not json", ("mapping", "INVALID_JSON")),
        ('["a"]', ("mapping", "INVALID_JSON")),
        ({"Nope": "email"}, ("mapping", "UNKNOWN_COLUMN")),
        ({"name": "nope"}, ("mapping", "UNKNOWN_FIELD")),
        ({"name": "email", "email": "email"}, ("mapping", "DUPLICATE_FIELD")),
        ({"name": "company_name"}, ("mapping", "NO_COMPANY_KEY")),
    ],
)
def test_bad_mappings_are_refused(admin_api, channel, mapping, expected):
    assert issue(import_now(admin_api, C9, mapping=mapping)) == expected
    assert not ImportBatch.objects.exists()


def test_preview_warns_where_the_import_refuses(admin_api, channel):
    body = preview(admin_api, C9, mapping={"name": "company_name", "domain": "ignore", "email": "ignore"}).json()
    assert [warning["code"] for warning in body["warnings"]] == ["no_company_key"]
    assert body["rows"][0]["skip_reason"] == "no_domain_no_email"
    assert body["columns"][1]["reason"] == "user" and body["columns"][1]["level"] == "certain"
    assert issue(preview(admin_api, C9, mapping={"x": "email"})) == ("mapping", "UNKNOWN_COLUMN")


def test_preview_rows_carry_the_skip_reason(admin_api, channel):
    body = preview(admin_api, "Firma,E-mail\nAlfa,nie-email\nBeta,ola@beta.pl\n").json()
    assert [row["skip_reason"] for row in body["rows"]] == ["invalid_email", None]
    assert body["warnings"] == []


def test_preview_writes_nothing(admin_api, channel):
    preview(admin_api, C9)
    assert not Company.objects.exists() and not ImportBatch.objects.exists()


def test_viewer_without_staff_cannot_preview(customer_api, channel):
    assert preview(customer_api, C9).status_code == 403


# --- AI --------------------------------------------------------------------------------------------------------------


def test_ai_is_off_by_default(admin_api, channel):
    with mock.patch.object(import_mapping_service, "ai_complete") as ai:
        columns_of(preview(admin_api, "domain,qwerty\na.pl,Ogrodnictwo\n"))
    ai.assert_not_called()


def test_ai_names_only_the_unrecognised_columns(admin_api, channel, monkeypatch):
    monkeypatch.setattr(leads_settings, "LEADS_IMPORT_AI_MODEL", "fake-model")
    answer = [{"name": "qwerty", "field": "industry"}, {"name": "domain", "field": "email"}]
    with mock.patch.object(import_mapping_service, "ai_complete", return_value=answer) as ai:
        columns = columns_of(preview(admin_api, "domain,qwerty\na.pl,Ogrodnictwo\n"))
    model, sent, allowed = ai.call_args.args
    assert model == "fake-model" and sent == [{"name": "qwerty", "samples": ["Ogrodnictwo"]}]
    assert "domain" not in allowed
    assert (columns["qwerty"]["field"], columns["qwerty"]["source"], columns["qwerty"]["level"]) == (
        "industry",
        "ai",
        "check",
    )
    assert columns["domain"]["field"] == "domain"


def test_ai_failure_leaves_the_column_unrecognised(admin_api, channel, monkeypatch):
    from django_utils.toolbox.errors import ToolboxError

    monkeypatch.setattr(leads_settings, "LEADS_IMPORT_AI_MODEL", "fake-model")
    with mock.patch("django_utils.toolbox.client.ToolboxClient.complete", side_effect=ToolboxError(503, "down")):
        columns = columns_of(preview(admin_api, "domain,qwerty\na.pl,Ogrodnictwo\n"))
    assert columns["qwerty"]["reason"] == "unrecognised"
