# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_no_missing_migrations():
    call_command("makemigrations", "django_prospecting", "--check", "--dry-run", verbosity=0)
