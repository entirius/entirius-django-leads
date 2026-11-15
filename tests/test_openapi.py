# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import pytest
from django.core.management import call_command


@pytest.mark.parametrize("urlconf", ["django_leads.urls", "django_prospecting.urls"])
def test_openapi_schema_validates(tmp_path, urlconf):
    call_command(
        "spectacular",
        "--urlconf",
        urlconf,
        "--validate",
        "--fail-on-warn",
        "--file",
        str(tmp_path / "schema.yaml"),
    )
