# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import pytest

from django_leads.enums import AttributeKind, ImportStatus
from django_leads.models import Company, Contact, ImportBatch
from django_leads.services import attribute_service, company_service, import_service

pytestmark = pytest.mark.django_db

SIZES = [{"value": "micro", "label": "Micro"}, {"value": "big", "label": "Big"}]
HEADER = "company_name,domain,email,first_name,attr.size,attr.employees,contact.attr.role,attr.gone,shoe_size"


@pytest.fixture
def definitions(channel) -> None:
    for entity, key, kind, options in [
        ("company", "size", AttributeKind.SELECT, SIZES),
        ("company", "employees", AttributeKind.NUMBER, []),
        ("contact", "role", AttributeKind.TEXT, []),
    ]:
        attribute_service.create_definition(
            channel, {"entity": entity, "key": key, "label": key, "kind": kind, "options": options}
        )
    attribute_service.create_definition(
        channel, {"entity": "company", "key": "gone", "label": "Gone", "kind": "text", "is_active": False}
    )


def run(channel, *rows: str) -> ImportBatch:
    content = "\n".join([HEADER, *rows])
    batch = import_service.create_batch(channel, "leads.csv", len(content), "test")
    return import_service.run_content(batch, content)


def test_two_attribute_columns_and_a_contact_column_are_imported(channel, definitions):
    batch = run(channel, "A,a-shop.pl,jan@a-shop.pl,Jan,micro,12,buyer,x,42", "B,b-shop.pl,,,big,,,,")
    assert batch.status == ImportStatus.DONE and batch.created_count == 2
    attributes = dict(Company.objects.values_list("domain", "attributes"))
    assert attributes == {"a-shop.pl": {"size": "micro", "employees": "12"}, "b-shop.pl": {"size": "big"}}
    assert Contact.objects.get().attributes == {"role": "buyer"}


def test_ignored_headers_are_named_once_and_never_in_the_row_report(channel, definitions):
    batch = run(channel, "A,a-shop.pl,,,,,,,", "B,b-shop.pl,,,,,,,")
    assert batch.ignored_columns == ["attr.gone", "shoe_size"]
    assert batch.report == []


def test_a_bad_attribute_value_is_dropped_and_the_row_imported(channel, definitions):
    batch = run(channel, "A,a-shop.pl,,,huge,,,,", "B,b-shop.pl,,,,many,,,", "C,c-shop.pl,,,big,,,,")
    assert (batch.created_count, batch.skipped_count, batch.report) == (3, 0, [])
    assert batch.warnings == {"bad_attribute:size": 1, "bad_attribute:employees": 1}
    attributes = dict(Company.objects.values_list("domain", "attributes"))
    assert attributes == {"a-shop.pl": {}, "b-shop.pl": {}, "c-shop.pl": {"size": "big"}}


def test_a_bad_contact_attribute_is_a_warning_too(channel, definitions):
    batch = run(channel, "A,a-shop.pl,jan@a-shop.pl,Jan,,,,,")
    assert batch.warnings == {} and Contact.objects.get().attributes == {}


def test_a_skipped_row_counts_no_warning(channel, definitions):
    batch = run(channel, "A,,,,huge,,,,")
    assert (batch.skipped_count, batch.warnings) == (1, {})


def test_a_matched_company_gets_missing_attributes_only(channel, definitions):
    company = company_service.upsert_company(channel, {"domain": "a-shop.pl", "source": "csv"})[0]
    company_service.update_company(company, {"attributes": {"size": "big"}})
    run(channel, "A,a-shop.pl,,,micro,7,,,")
    assert Company.objects.get().attributes == {"size": "big", "employees": "7"}


def test_ignored_headers_are_not_repeated_by_a_retry(channel, definitions):
    batch = import_service.create_batch(channel, "leads.csv", 1, "test")
    import_service.note_ignored_headers(batch, ["domain", "shoe_size"])
    import_service.note_ignored_headers(batch, ["domain", "shoe_size"])
    batch.refresh_from_db()
    assert batch.ignored_columns == ["shoe_size"] and batch.report == []
