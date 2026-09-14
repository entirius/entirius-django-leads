# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from unittest import mock

import pytest

from django_leads.utils import domains
from django_leads.utils.domains import email_domain, registrable_domain
from django_leads.utils.emails import normalize_email


@pytest.mark.parametrize(
    "raw", ["lesna-shop.pl", "www.lesna-shop.pl/pl", "https://sklep.lesna-shop.pl/x?y", " LESNA-SHOP.PL "]
)
def test_L01_registrable_domain_strips_subdomains_paths_and_case(raw):
    assert registrable_domain(raw) == "lesna-shop.pl"


def test_L01_public_suffix_is_respected():
    """`shop.pl` itself is a public suffix — its sites are separate companies, as the PSL says."""
    assert registrable_domain("www.ogrod.shop.pl") == "ogrod.shop.pl"


def test_L02_registrable_domain_never_merges_a_substring():
    assert registrable_domain("myogrod.pl") != registrable_domain("ogrod.pl")


def test_reserved_test_tld_is_registrable():
    assert registrable_domain("jan.example-shop-2.test") == "example-shop-2.test"


@pytest.mark.parametrize("raw", ["", "localhost", "not a domain", "pl", "10.0.0.1"])
def test_registrable_domain_rejects_input_without_a_registrable_part(raw):
    with pytest.raises(ValueError):
        registrable_domain(raw)


def test_L03_normalize_email_strips_and_lower_cases():
    assert normalize_email("  Jan.Kowalski@Ogrod.PL ") == "jan.kowalski@ogrod.pl"


def test_email_domain_is_the_registrable_host():
    assert email_domain("jan@mail.ogrod.pl") == "ogrod.pl"


def test_registrable_domain_never_hits_network():
    with mock.patch("tldextract.cache.DiskCache.cached_fetch_url") as fetch:
        assert domains.build_extractor()("www.ogrod.pl").top_domain_under_public_suffix == "ogrod.pl"
    fetch.assert_not_called()
