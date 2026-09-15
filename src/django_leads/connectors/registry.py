# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Connectors by key from the `leads_connectors` entry points — other packages can register their own."""

from importlib.metadata import entry_points

from django_leads.connectors.base import Connector

GROUP = "leads_connectors"


def get_connector(key: str, **kwargs) -> Connector:
    """An instance of the connector registered as `key`; `LookupError` when none is."""
    matches = entry_points(group=GROUP, name=key)
    if not matches:
        raise LookupError(f"no leads connector registered as {key!r}")
    return next(iter(matches)).load()(**kwargs)
