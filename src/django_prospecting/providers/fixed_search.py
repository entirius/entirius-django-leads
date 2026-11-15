# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""The same three people for every company, no network: test stacks (zeno) and demos. Each person leaves one
check open, so every result of a check can be seen: a role without a fit, an unknown employment."""

from django_prospecting.providers.base import FoundAddress, FoundPerson, SearchAnswer

SOURCE = "fixed test candidates (no search ran)"


class FixedSearch:
    def find_people(self, *, company_name: str, domain: str, channel_idx: str) -> SearchAnswer:
        people = [
            FoundPerson(
                first_name="Ewa", last_name="Zielińska", job_title="Head of E-commerce", role_fits=True,
                still_employed=True, profile_url=f"https://{domain}/team", source=f"{SOURCE}: {domain}/team",
            ),
            FoundPerson(
                first_name="Marek", last_name="Wójcik", job_title="Office Manager", role_fits=None,
                still_employed=True, source=f"{SOURCE}: {domain}/contact",
            ),
            FoundPerson(
                first_name="Tomasz", last_name="Lis", job_title="CEO", role_fits=True, still_employed=None,
                source=f"{SOURCE}: public register",
            ),
        ]  # fmt: skip
        addresses = [
            FoundAddress(email=f"office@{domain}", source=f"{SOURCE}: {domain}/contact"),
            FoundAddress(
                email=f"jan.nowak@{domain}", first_name="Jan", last_name="Nowak", source=f"{SOURCE}: {domain}/press"
            ),
        ]
        return SearchAnswer(people=people, addresses=addresses)
