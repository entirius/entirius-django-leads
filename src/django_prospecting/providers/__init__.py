# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Replaceable providers: who finds people (`search`) and who confirms a mailbox without sending (`tester`)."""

from importlib import import_module

from django_prospecting import settings as prospecting_settings


def _load(path: str):
    module, _, name = path.rpartition(".")
    return getattr(import_module(module), name)()


def search_provider():
    name = prospecting_settings.PROSPECTING_SEARCH_PROVIDER
    if name == "toolbox":
        from django_prospecting.providers.toolbox_search import ToolboxSearch

        return ToolboxSearch()
    if name == "fixed":
        from django_prospecting.providers.fixed_search import FixedSearch

        return FixedSearch()
    return _load(name)


def address_tester():
    name = prospecting_settings.PROSPECTING_ADDRESS_TESTER
    if name == "stub":
        from django_prospecting.providers.stub_tester import StubTester

        return StubTester()
    return _load(name)
