# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Registrable-domain normalisation — the company dedup key. Never import this across modules.

Uses the public suffix list bundled with `tldextract` (no download). The reserved `.test` TLD (RFC 2606)
is added so sandbox fixtures dedup like real domains.
"""

import tldextract

RESERVED_SUFFIXES = ("test",)


def build_extractor() -> tldextract.TLDExtract:
    return tldextract.TLDExtract(
        cache_dir=None, suffix_list_urls=(), fallback_to_snapshot=True, extra_suffixes=RESERVED_SUFFIXES
    )


_EXTRACT = build_extractor()


def registrable_domain(url_or_domain: str) -> str:
    """`www.ogrod.pl/pl`, `https://sklep.ogrod.pl/x?y` → `ogrod.pl`; `ValueError` without a registrable part."""
    domain = _EXTRACT(url_or_domain.strip().lower()).top_domain_under_public_suffix
    if not domain:
        raise ValueError("no registrable domain")
    return domain


def email_domain(email: str) -> str:
    """Registrable domain of an address's host; `ValueError` when there is none."""
    _, _, host = email.rpartition("@")
    return registrable_domain(host)
