# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Companies — placeholder: rows added by hand, nothing searches for them yet."""

from django_prospecting.models import CompanyProspect


def list_companies(channel_idx: str):
    return CompanyProspect.objects.filter(channel_idx=channel_idx)


def create(*, channel_idx: str, name: str, domain: str = "", note: str = "") -> CompanyProspect:
    return CompanyProspect.objects.create(channel_idx=channel_idx, name=name, domain=domain.lower(), note=note)
