# entirius-django-leads

A Django module for leads and the B2B sales pipeline of Entirius, the open-source modular ecommerce platform.
It keeps companies and contacts, moves them through stages, runs outreach rules and handles retention and GDPR.

[![PyPI](https://img.shields.io/pypi/v/entirius-django-leads.svg)](https://pypi.org/project/entirius-django-leads/)
[![Python](https://img.shields.io/pypi/pyversions/entirius-django-leads.svg)](https://pypi.org/project/entirius-django-leads/)
[![License: MPL-2.0](https://img.shields.io/badge/license-MPL--2.0-blue.svg)](https://github.com/entirius/entirius-django-leads/blob/develop/LICENSE)

## What it does

- Imports companies and contacts from CSV and contact forms, with deduplication by domain and email.
- Moves companies through configurable pipeline stages with a full activity timeline.
- Runs stage rules: AI-assisted site analysis, recipient selection and outreach drafts behind a consent gate.
- Anonymises idle contacts on a retention schedule and serves GDPR export and erase requests.
- Exposes an admin API (JWT, Pydantic schemas, OpenAPI) for the CMS.

## Installation

```shell
uv add entirius-django-leads
pip install entirius-django-leads
```

Optional extras: `entirius-django-leads[contact-forms]` (form submissions become contacts),
`entirius-django-leads[notifications]` (failure notifications to sales admins).

```python
INSTALLED_APPS = [
    # ...
    "django_utils",
    "django_regional",
    "django_agreements",
    "django_siteintel",
    "django_communicator",
    "django_leads",
]
```

Settings, queue and bootstrap order: `docs/install.md` —
https://github.com/entirius/entirius-django-leads/blob/develop/docs/install.md

## Quick start

```python
# main/urls.py
from django.urls import include, path

urlpatterns.append(path("", include("django_leads.urls")))
```

The admin API is then mounted under `api/leads/v2/admin/`; the endpoints are described in
https://github.com/entirius/entirius-django-leads/blob/develop/docs/api.md

## Requirements

- Python 3.11 or newer, Django 4.2 or newer.
- PostgreSQL in production (tests fall back to sqlite).
- A Celery worker consuming the `leads_default` queue.
- Entirius packages pulled in: `entirius-django-utils`, `entirius-django-regional`, `entirius-django-agreements`,
  `entirius-django-communicator`, `entirius-django-siteintel`.

## Documentation

- Module docs: https://github.com/entirius/entirius-django-leads/tree/develop/docs
- Entirius documentation: https://docs.entirius.com/
- Changelog: https://github.com/entirius/entirius-django-leads/blob/develop/CHANGELOG.md

## Part of Entirius

This module runs inside the Entirius backend service (Volkanos) and drafts its outreach through the communicator.

- Backend service: https://github.com/entirius/entirius-service-volkanos
- Docker harness: https://github.com/entirius/entirius-zeno
- Sibling modules: https://github.com/entirius/entirius-django-communicator, https://github.com/entirius/entirius-django-siteintel,
  https://github.com/entirius/entirius-django-agreements

## Development

```shell
make install     # sync dependencies (uv, incl. extras)
make check       # lint + format check (ruff)
make test        # test suite (pytest; PostgreSQL with DATABASE_URL, else sqlite)
```

Development and agent instructions: https://github.com/entirius/entirius-django-leads/blob/develop/AGENTS.md

## License

Mozilla Public License 2.0 (MPL-2.0) — see https://github.com/entirius/entirius-django-leads/blob/develop/LICENSE.
