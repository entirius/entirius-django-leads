---
title: Prospecting
description: The django_prospecting app of the package — new people to contact at a company that lost its last person to mail, and a placeholder for new companies.
---

`django_prospecting` ships in `entirius-django-leads` next to `django_leads` and is enabled only when the host lists it
in `INSTALLED_APPS`. App label and table prefix `django_prospecting`, URL prefix `api/prospecting/v2/`, queue
`prospecting_default`, access areas `prospecting.people` (pii) and `prospecting.companies`.

## What it does

- **People**: for one company, finds a few people through a public search (no logged-in session of any site),
  works out the company's address pattern (`{first}.{last}@`) from published addresses, guesses each person's
  address and asks an address tester — without sending a mail — whether the mailbox exists.
- Every person gets four checks: role fits the offer, address known, still works there, address works. Each check
  is tried up to `PROSPECTING_MAX_ATTEMPTS` searches (3); still missing = `no_info`, shown to the salesperson as a
  missing confirmation. Every piece of information keeps its source and date.
- A salesperson accepts or rejects each candidate. Only an accepted person reaches leads, as a contact
  (`candidate_service.accept_candidate` of leads, source `prospecting`, legal basis legitimate interest); nothing here
  sends mail. Leads never learns how the person was found.
- The `contact_needed` signal of leads (a company without anyone left to mail: wrong person, opted out) starts a search.
- **Companies**: placeholder — a list of companies added by hand; no search runs yet.
- Limits: searches per company, provider calls per channel and day, candidates per search.

## Concept

**Search.** One provider call returns people (name, job title, role fits, still employed, profile, source) and the
company's published addresses. Published addresses with a known owner give the pattern (`{first}.{last}`); every
person gets up to `PROSPECTING_MAX_ADDRESSES` guesses, the company's pattern first. Each guess is tested once per
final verdict (`valid`, `invalid`, `catch_all`) without sending a mail; `unknown` is tested again on the next call.

**Checks.** `role` (fits the offer), `email` (from the person, the pattern, or a guess the tester confirmed),
`employed`, `mailbox` (a tester `valid`; a domain that accepts everything stays `no_info`). The search calls the
provider again while any check is `no_info`, at most `PROSPECTING_MAX_ATTEMPTS` times.

**Public sources only.** Public profiles through a search engine, the company's website, public registers — never a
logged-in session (decision of 15.11.2026).

**Not yet (iteration 2).** Finding new companies (today a list added by hand); a real address tester (an external
service, so our mail server does not land on blocklists); limits editable in the CMS; removing rejected candidates
after a retention period.

## Install

```python
INSTALLED_APPS = [
    # ...
    "django_leads",
    "django_prospecting",  # after django_leads
]

# main/urls.py
urlpatterns.append(path("", include("django_prospecting.urls")))
```

`manage.py migrate django_prospecting`. A Celery worker consumes `prospecting_default` (task
`django_prospecting.run_search`, started on commit of a new search). No beat entry. Without `django_leads` in
`INSTALLED_APPS` the app still runs searches, but an accept answers 503 and no search starts by itself.

| Setting | Default | Meaning |
|---|---|---|
| `PROSPECTING_QUEUE_DEFAULT` | `prospecting_default` | queue of `run_search` |
| `PROSPECTING_SEARCH_PROVIDER` | `toolbox` | `toolbox` (public search through the AI toolbox, tag `prospecting.find_people`), `fixed` (the same three people for every company, no network — test stacks) or a dotted path to a class with `find_people(company_name, domain, channel_idx)` |
| `PROSPECTING_SEARCH_MODEL` | `""` | toolbox model of the public search (required for `toolbox`) |
| `PROSPECTING_ADDRESS_TESTER` | `stub` | `stub` (rules on the address, no network — test stacks only) or a dotted path to a class with `check(email)` answering `valid`, `invalid`, `catch_all` or `unknown` |
| `PROSPECTING_MAX_ATTEMPTS` | `3` | searches per check before `no_info` |
| `PROSPECTING_COMPANY_SEARCH_LIMIT` | `3` | searches per company, all time (refused ones not counted) |
| `PROSPECTING_DAILY_BUDGET` | `100` | provider calls per channel and day |
| `PROSPECTING_MAX_CANDIDATES` | `5` | candidates kept per search |
| `PROSPECTING_MAX_ADDRESSES` | `3` | guessed addresses tested per person |

Read once, at import (like `django_leads.settings`): tests monkeypatch `django_prospecting.settings`. The toolbox
provider needs the host's `AI_TOOLBOX_*` settings (`django_utils.toolbox`). A real address tester is an external
service on purpose: many probes from our own mail server land it on blocklists and hurt the delivery of every other
mail.

## API

Base `api/prospecting/v2/admin/<channel_idx>/`, JWT, staff only. Errors in the v2 shape of `django_utils`.

| Method | Path | What |
|---|---|---|
| GET / POST | `companies/` | placeholder list with its `description`; add a company by hand |
| GET | `people/searches/?subject_ref=` | searches, newest first |
| POST | `people/searches/` | `{subject_ref, company_name, domain}` → 201 search (`queued`, or `refused` over the company limit) |
| GET | `people/candidates/?subject_ref=&status=` | candidates with `checks` (kind, result `ok`/`no_info`, value, source, checked_at, attempts), `missing`, `email_options` (email, source, verdict, tested_at, tested_by) |
| POST | `people/candidates/<id>/accept/` | `{email?}` → the person becomes a contact in leads; 409 decided, 400 refused by leads, 503 leads not installed |
| POST | `people/candidates/<id>/reject/` | `{reason?}`; 409 decided |
| GET | `people/limits/` | providers, limits, `spent_today` |

## Architecture

```
src/django_prospecting/
├── apps.py (areas, connects the leads receiver)  enums.py  settings.py (read at import)  admin.py (read-only)
├── models/     people_search  candidate  candidate_check  company_prospect
├── providers/  base (SearchAnswer contract)  toolbox_search  fixed_search  stub_tester
├── services/   search_service (start, run, checks)  pattern_service  candidate_service  leads_bridge  company_service
├── signals/    leads_receivers (contact_needed → start)
├── schemas/    requests.py  responses.py
├── api/admin/  views/people_views.py  views/company_views.py  urls.py
└── tasks/      search.py (run_search)        queue `prospecting_default`
```

## Rules

- Only `services/leads_bridge.py` and `signals/leads_receivers.py` import `django_leads`, lazily, behind
  `apps.is_installed("django_leads")`. `subject_ref` is never parsed here.
- Never log in to any site to find people; only public sources.
- The fake AI toolbox knows no `prospecting.find_people` fixture: it answers its default text and every attempt fails
  with "no JSON object". Test stacks use `PROSPECTING_SEARCH_PROVIDER = "fixed"`.
- The `stub` tester never touches the network; it must never run in production — it says `valid` for every
  `first.last@` address.
- `spent_today` counts attempts of searches created since the server's midnight.

## Tests

`tests/prospecting/` (PostgreSQL via `DATABASE_URL`). The bridge to leads is mocked; `without_leads` (conftest) takes
`django_leads` out of `apps.is_installed` for the 503 path.

| File | Covers |
|---|---|
| `test_pattern.py` | name folding, pattern inference, guesses |
| `test_search.py` | fixed search, 3 attempts → `no_info`, sources and dates, catch-all, retries, budget, limits, start on commit |
| `test_providers.py` | stub tester, toolbox request and its errors |
| `test_candidates_api.py` | API: list, accept, reject, searches, limits, companies, auth |
| `test_leads_bridge.py` | bridge without leads, `contact_needed` receiver |
| `test_migrations.py`, `test_access_ownership.py` | contracts (`tests/test_openapi.py` and `tests/test_version.py` cover the app too) |

BDD (U3): `@prospecting` in `entirius-test-package-emporium` (`features/leads/prospecting.feature`), run by the
entirius-zeno harness with `PROSPECTING_SEARCH_PROVIDER = "fixed"`.
