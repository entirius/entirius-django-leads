# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The module owns its access rules: areas on its AppConfig, an `access_area` on every admin view."""

import pytest

pytest.importorskip("django_access")

from django_access.testing import assert_routes_covered  # noqa: E402


def test_admin_routes_are_covered_by_own_declarations():
    assert_routes_covered("django_prospecting", urlconf="django_prospecting.urls", require_own=True)
