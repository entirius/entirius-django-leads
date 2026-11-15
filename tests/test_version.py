# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""munin reports `__version__` of every app; each is the version the package is released as."""

import tomllib
from pathlib import Path

import pytest

import django_leads
import django_prospecting

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


@pytest.mark.parametrize("package", [django_leads, django_prospecting], ids=lambda p: p.__name__)
def test_version_matches_pyproject(package):
    assert package.__version__ == tomllib.loads(PYPROJECT.read_text())["project"]["version"]
