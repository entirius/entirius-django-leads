# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import pytest

from django_prospecting.services import leads_bridge
from django_prospecting.signals import leads_receivers
from tests.prospecting.conftest import CHANNEL_IDX, SUBJECT

pytestmark = pytest.mark.django_db


def test_bridge_without_leads_is_unavailable(fixed_provider, without_leads):
    from tests.prospecting.conftest import run_search

    candidate = run_search().candidates.first()
    with pytest.raises(leads_bridge.LeadsUnavailable):
        leads_bridge.add_contact(candidate, email="a.b@shop.test", actor="anna")


def test_contact_needed_receiver_starts_a_search(fixed_provider, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        leads_receivers.on_contact_needed(
            sender=None, subject_ref=SUBJECT, channel_idx=CHANNEL_IDX, company_name="Example Shop",
            domain="example-shop.test", reason="wrong_person",
        )  # fmt: skip
    from django_prospecting.models import PeopleSearch

    search = PeopleSearch.objects.get(subject_ref=SUBJECT)
    assert (search.reason, search.requested_by, search.status) == ("wrong_person", "leads", "done")
    assert search.candidates.count() == 3
