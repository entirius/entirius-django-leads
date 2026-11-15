# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from django_prospecting.providers.base import FoundAddress
from django_prospecting.services import pattern_service


def test_fold_drops_diacritics_and_spaces():
    assert pattern_service.fold("Zielińska") == "zielinska"
    assert pattern_service.fold("Łukasz") == "lukasz"
    assert pattern_service.fold("Kowalska-Nowak") == "kowalskanowak"


def test_infer_takes_the_pattern_of_addresses_with_a_known_owner():
    addresses = [
        FoundAddress(email="office@shop.test", source="site"),
        FoundAddress(email="j.nowak@shop.test", first_name="Jan", last_name="Nowak", source="press page"),
        FoundAddress(email="a.lis@shop.test", first_name="Anna", last_name="Lis"),
        FoundAddress(email="ewa.kot@other.test", first_name="Ewa", last_name="Kot"),
    ]
    found = pattern_service.infer(addresses, "shop.test")
    assert found.pattern == "{f}.{last}"
    assert "j.nowak@shop.test (press page)" == found.source


def test_infer_without_owner_or_match_is_none():
    assert pattern_service.infer([FoundAddress(email="office@shop.test")], "shop.test") is None


def test_guesses_put_the_company_pattern_first_and_stop_at_the_limit():
    assert pattern_service.guesses("Ewa", "Zielińska", "Shop.test", "{f}{last}", 3) == [
        "ezielinska@shop.test",
        "ewa.zielinska@shop.test",
        "e.zielinska@shop.test",
    ]
    assert pattern_service.guesses("Ewa", "Zielińska", "shop.test", "", 2) == [
        "ewa.zielinska@shop.test",
        "e.zielinska@shop.test",
    ]
