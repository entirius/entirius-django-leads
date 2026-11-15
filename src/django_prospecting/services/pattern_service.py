# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Company address patterns: work out how a company builds addresses from published ones, then guess a person's.

Addresses of people are rarely published; the pattern (`{first}.{last}@`) is, through any one address whose owner
is known. Names are folded to ASCII (`Zielińska` → `zielinska`) the way addresses are written."""

import unicodedata
from collections import Counter
from dataclasses import dataclass

from django_prospecting.providers.base import FoundAddress

# Most common first: the order guesses are tried in when the company's pattern is unknown.
PATTERNS = ("{first}.{last}", "{f}.{last}", "{first}", "{first}{last}", "{f}{last}", "{last}.{first}", "{first}_{last}")
FOLD = str.maketrans({"ł": "l", "Ł": "L", "ø": "o", "ß": "ss"})


@dataclass(frozen=True)
class Pattern:
    pattern: str
    source: str


def fold(name: str) -> str:
    """Lower-case ASCII letters only: diacritics dropped, spaces and hyphens removed."""
    text = unicodedata.normalize("NFKD", name.translate(FOLD))
    return "".join(char for char in text.lower() if char.isascii() and char.isalpha())


def local_part(pattern: str, first_name: str, last_name: str) -> str:
    first, last = fold(first_name), fold(last_name)
    return pattern.format(first=first, last=last, f=first[:1])


def infer(addresses: list[FoundAddress], domain: str) -> Pattern | None:
    """The pattern most published addresses of the domain with a known owner follow; None without such an address."""
    hits: Counter[str] = Counter()
    evidence: dict[str, str] = {}
    for address in addresses:
        local, _, host = address.email.lower().partition("@")
        if host != domain.lower() or not (address.first_name and address.last_name):
            continue
        for pattern in PATTERNS:
            if local_part(pattern, address.first_name, address.last_name) == local:
                hits[pattern] += 1
                evidence.setdefault(pattern, f"{address.email} ({address.source})" if address.source else address.email)
                break
    if not hits:
        return None
    pattern = hits.most_common(1)[0][0]
    return Pattern(pattern, evidence[pattern])


def guesses(first_name: str, last_name: str, domain: str, pattern: str, limit: int) -> list[str]:
    """The company's pattern first, then the common ones; no duplicates, at most `limit`."""
    order = [pattern, *PATTERNS] if pattern else list(PATTERNS)
    addresses: list[str] = []
    for item in order:
        local = local_part(item, first_name, last_name)
        address = f"{local}@{domain.lower()}"
        if local and address not in addresses:
            addresses.append(address)
    return addresses[:limit]
