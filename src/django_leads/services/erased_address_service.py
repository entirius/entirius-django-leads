# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Memory of erased and anonymised addresses by token: they are never imported or re-created from a form."""

from collections.abc import Iterable

from django_leads.models import ErasedAddress
from django_leads.utils.emails import anonymised_address, normalize_email

SKIP_REASON = "erased_address"


def remember(email: str) -> None:
    """Idempotent; a blank address has no token to remember."""
    if not normalize_email(email):
        return
    ErasedAddress.objects.bulk_create([ErasedAddress(token=anonymised_address(email))], ignore_conflicts=True)


def erased_emails(emails: Iterable[str]) -> set[str]:
    """The normalised addresses among `emails` whose token is listed — one select, none without an address."""
    by_token = {anonymised_address(email): normalize_email(email) for email in emails if normalize_email(email)}
    if not by_token:
        return set()
    found = ErasedAddress.objects.filter(token__in=list(by_token)).values_list("token", flat=True)
    return {by_token[token] for token in found}


def is_erased(email: str) -> bool:
    return bool(erased_emails([email]))
