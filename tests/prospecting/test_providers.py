# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from unittest import mock

import pytest
from django_utils.toolbox import ToolboxConnectionError

from django_prospecting import settings as prospecting_settings
from django_prospecting.enums import AddressVerdict
from django_prospecting.providers.base import ProviderError
from django_prospecting.providers.stub_tester import StubTester
from django_prospecting.providers.toolbox_search import TAG, ToolboxSearch


def test_stub_tester_rules():
    tester = StubTester()
    assert tester.check("ewa.zielinska@shop.test") == AddressVerdict.VALID
    assert tester.check("e.zielinska@shop.test") == AddressVerdict.INVALID
    assert tester.check("ewa.zielinska@catchall.shop.test") == AddressVerdict.CATCH_ALL


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr(prospecting_settings, "PROSPECTING_SEARCH_MODEL", "fake-chat")


def _client(response=None, error=None):
    client = mock.MagicMock()
    client.__enter__.return_value = client
    if error:
        client.complete.side_effect = error
    else:
        client.complete.return_value = mock.Mock(parsed=response)
    return mock.patch("django_prospecting.providers.toolbox_search.ToolboxClient", return_value=client), client


def test_toolbox_search_asks_with_the_schema_and_validates(model):
    answer = {"people": [{"first_name": "Ewa", "last_name": "Kot", "role_fits": True}], "addresses": []}
    patcher, client = _client(answer)
    with patcher:
        result = ToolboxSearch().find_people(company_name="Shop", domain="shop.test", channel_idx="ch")
    assert result.people[0].last_name == "Kot"
    request = client.complete.call_args.args[0]
    assert request.tags == [TAG, "channel:ch"] and request.json_schema and request.model == "fake-chat"
    assert "without logging in" in request.messages[0].content


@pytest.mark.parametrize("parsed", [None, {"people": [{"first_name": ""}]}])
def test_toolbox_search_unusable_answer_is_a_provider_error(model, parsed):
    patcher, _ = _client(parsed)
    with patcher, pytest.raises(ProviderError):
        ToolboxSearch().find_people(company_name="Shop", domain="shop.test", channel_idx="ch")


def test_toolbox_error_is_a_provider_error(model):
    patcher, _ = _client(error=ToolboxConnectionError(0, "down"))
    with patcher, pytest.raises(ProviderError, match="ToolboxConnectionError"):
        ToolboxSearch().find_people(company_name="Shop", domain="shop.test", channel_idx="ch")


def test_toolbox_search_needs_a_model(monkeypatch):
    monkeypatch.setattr(prospecting_settings, "PROSPECTING_SEARCH_MODEL", "")
    with pytest.raises(ProviderError, match="MODEL"):
        ToolboxSearch().find_people(company_name="Shop", domain="shop.test", channel_idx="ch")
