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
- An optional app in the same package, enabled only when listed in `INSTALLED_APPS`: `django_ai_crm` — a
  salesperson's day plan and an agent that updates the CRM, over a REST API v2 and an MCP server for the terminal,
  Claude Code and ChatGPT (`docs/ai-crm.md`).

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

## Connect from the terminal, Claude Code and ChatGPT

With `django_ai_crm` enabled, a salesperson works with their CRM from any client through one set of tools: the REST
API v2 and an MCP server under `api/ai-crm/v2/admin/<channel_idx>/`. Every call acts as the salesperson whose token
it carries: their tasks, companies and deals only, their access roles (django-access) decide what they may change,
and every change is signed `"<username> via ai-crm (api | mcp)"` in the History. A lost deal, Do not contact and
sending a draft come back as a proposal that needs their yes (`confirm_proposal`).

**The salesperson's token.** Their own login, through the service's JWT endpoint — the same token the CMS uses. The
access token lives as long as the service's `SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"]` (5 minutes by default), the refresh
token a day; `POST api/token/refresh/` with `{"refresh": ...}` gives a new access token. A staff user without a role
is stopped by the access gate (403).

```shell
HOST=https://<service-host>   # http://localhost:8100 in the entirius-zeno harness
CHANNEL=<channel_idx>         # default-europe in zeno
curl -s $HOST/api/token/ -H 'Content-Type: application/json' \
  -d '{"username": "anna", "password": "..."}' > ~/.entirius-token.json   # {"refresh": ..., "access": ...}
TOKEN=$(jq -r .access ~/.entirius-token.json)
```

**Terminal (REST API v2).**

```shell
curl -s "$HOST/api/ai-crm/v2/admin/$CHANNEL/plan/?tz=Europe/Warsaw&language=en" -H "Authorization: Bearer $TOKEN"
# {"items": [{"type": "task", "task_id": 113, "title": "Send the price list", "overdue": true, "n": 1, ...}, ...],
#  "upcoming_7d": 2, "text": "Your day, in order:\n1. [overdue] Send the price list — Example Shop 3 (task #113)..."}
curl -s "$HOST/api/ai-crm/v2/admin/$CHANNEL/tools/close_task/" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"arguments": {"task_id": 113, "outcome": "sent"}, "source": "api"}'
```

`GET tools/` lists every tool with the JSON Schema of its arguments; `POST chat/` takes a sentence
(`{"message": "done 1: called, interested"}`).

**Claude Code (MCP, streamable HTTP).**

```shell
claude mcp add --transport http entirius-crm "$HOST/api/ai-crm/v2/admin/$CHANNEL/mcp/" \
  --header "Authorization: Bearer $TOKEN"
claude mcp list   # entirius-crm: ... (HTTP) - ✔ Connected
```

Then ask Claude Code "What is on my plan today?" or "I called Example Shop, they want an offer — close it and remind
me on Friday". A fixed header stops working when the access token expires (401; Claude Code then tries OAuth and
reports a failed client registration): add the server again with a new token, or let Claude Code fetch one for every
connection with `headersHelper` — a command that prints the headers as JSON:

```shell
cat > ~/bin/entirius-headers <<'SH'
#!/bin/sh
curl -s "$HOST/api/token/refresh/" -H 'Content-Type: application/json' \
  -d "{\"refresh\": \"$(jq -r .refresh ~/.entirius-token.json)\"}" | jq -c '{Authorization: ("Bearer " + .access)}'
SH
chmod +x ~/bin/entirius-headers
claude mcp add-json entirius-crm "{\"type\": \"http\", \"url\": \"$HOST/api/ai-crm/v2/admin/$CHANNEL/mcp/\",
  \"headersHelper\": \"$HOME/bin/entirius-headers\"}"
```

(`HOST` inside the script must be set there or replaced by the address; Claude Code runs a `headersHelper` only in a
workspace whose trust dialog was accepted.)

**ChatGPT (custom connector).** ChatGPT connects to an MCP server from the internet, so it needs:

- the service on a **public HTTPS** address (a `localhost` or VPN-only service cannot be reached);
- the connector URL `https://<service-host>/api/ai-crm/v2/admin/<channel_idx>/mcp/`;
- authentication ChatGPT can do by itself: a connector cannot carry a fixed `Authorization` header and asks for
  OAuth. The service has no OAuth authorization server yet, so connecting ChatGPT needs one in front of the MCP
  route (OAuth 2.1 with dynamic client registration, issuing the salesperson's JWT) — never a public route without
  a token, which would act as anyone.

Any other MCP client that can send a header connects as Claude Code does.

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
