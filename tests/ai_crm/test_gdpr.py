# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import pytest
from django.contrib.auth import get_user_model

from django_ai_crm import gdpr
from django_ai_crm.enums import ProposalStatus, Source
from django_ai_crm.models import Proposal
from django_leads.gdpr import registry

pytestmark = pytest.mark.django_db


@pytest.fixture
def proposals(db):
    user = get_user_model().objects.create_user(username="sales")
    fields = {"channel_idx": "default-europe", "user": user, "tool": "approve_draft", "source": Source.CMS}
    mine = Proposal.objects.create(
        summary="send draft #3 to jan@shop.test", arguments={"to": "jan@shop.test"}, **fields
    )
    other = Proposal.objects.create(summary="send draft #4 to ola@shop.test", **fields)
    return mine, other


def test_leads_discovers_the_hooks():
    assert "django_ai_crm" in registry.discover()


def test_export_and_erase_touch_only_proposals_naming_the_address(proposals):
    mine, other = proposals
    assert [row["id"] for row in gdpr.gdpr_export("Jan@Shop.test")["Proposal"]] == [mine.pk]
    assert gdpr.gdpr_erase("jan@shop.test") == {"proposals": 1}
    mine.refresh_from_db()
    other.refresh_from_db()
    assert (mine.summary, mine.arguments, mine.status) == ("[erased]", {}, ProposalStatus.EXPIRED)
    assert other.summary.endswith("ola@shop.test") and other.status == ProposalStatus.PENDING
