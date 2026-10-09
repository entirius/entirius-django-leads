---
title: Testing
description: Which test file covers what, and the BDD and e2e suites in the entirius-zeno harness.
---

## Module suite

`make test` — pytest + pytest-django, `tests/settings.py`: `DATABASE_URL` when set (CI, zeno), else sqlite in
memory. The import query ceiling is exact only on PostgreSQL: in the entirius-zeno harness run
`make module-test MODULE=entirius-django-leads` (the same suite inside the service container).
Nothing leaves the process: `ToolboxClient` is patched in the services that call it, and `communicate()`
(with the agreements footer) is patched where a test only needs the call. `ENVIRONMENT = "development"` routes the `test/` endpoints.

`tests/test_openapi.py` fails on any schema warning; run it after touching a view or schema. `docs/openapi.yaml` is
exported from the service (`manage.py spectacular`) and filtered to `/api/leads/` — regenerate it with the API.

| File | Covers |
|---|---|
| `test_normalisers.py` | registrable domain (`.test`, no network), email normalisation |
| `test_services.py` | upserts fill empty only, first stage, whitelists, `transition_stage` timeline + signal on commit only, stage in use, consent without ref, concurrent form submissions → one company |
| `test_import.py` | report counts and dedup, skip reasons, overlong values, file-level failures, resume without replay, no CSV retained, capped report, legal-basis proposal and conflict, bulk-write fallback, 5 000 rows with a query ceiling, the command |
| `test_import_transport.py` | id-only task message, temp files deleted, `legacy_message`, sweep of temp files and stale batches, DB down on the final write |
| `test_bridge.py` | form lead → contact after commit, no consent, no lead creation rule, consent string values, free-mail and unknown channel, idempotent retry, broker down, sweep retry and `form_import_failed`, erased address |
| `test_rules.py` | primary draft, no email, `do_not_contact` before `communicate()`, cooldown, `require_email`, gate refusals of manual communicate, zero hooks, request-audit rule, rule errors, missing clause set |
| `test_pipeline.py` | `ai_pick` with stored reason, invalid pick fallback, analysis and `intel_ready`, empty sources, redelivery pays once, prompt injection, no toolbox call inside a transaction, locking vs opt-out, rotation, communicator receivers, won seam with and without accounts, intel retry after a toolbox outage (transient vs permanent, attempts cap, unreachable toolbox, expired audit, taken claim, no call inside a transaction, dev endpoint) |
| `test_retention.py` | retention selection and anonymisation, token blocks re-import, GDPR export/erase across modules, hook registry, API and command, blank email refused, concurrent erase + retention, hash and token parity with the communicator and agreements copies, connector registry |
| `test_admin_api.py` / `test_rules_api.py` | auth (403 for customers), filters, sort allowlist, conflicts, PATCH whitelists, stage delete 409, contacts (remove = delete or anonymise, one primary), uploads, consent ref, rules and profiles CRUD, prompt never listed, development endpoints and their 404 |
| `test_openapi.py` | `spectacular --validate --fail-on-warn` over `django_leads.urls` |

## BDD in zeno

In the entirius-zeno harness: `make seed`, `make bdd`, `make e2e-funnel`, `make e2e-accept` run the Emporium test
package, `features/leads/` (read-only from this repo). Needs `make dev`, `make seed` (`SEED OK`),
`make mail` and the AI toolbox up (`make toolbox-check`).

| Feature | Covers | One-shot |
|---|---|---|
| `leads_import.feature` | CSV import, form leads, consent | no |
| `leads_pipeline.feature` | rules, outreach gate, recipient pick, rotation | `@leads-oneshot` on rotation |
| `leads_gdpr.feature` | retention, GDPR erase | `@leads-oneshot` on both |
| `leads_funnel.feature` (`@funnel`) | reference scenario: CSV → audit → draft → review → send → reply → escalation, blocked contact | `@leads-oneshot` (whole feature) |

```bash
make bdd TAGS=@leads           # all leads features
make seed && make bdd TAGS=@funnel
```

A one-shot scenario consumes its data (rotation, anonymisation, erasure, the funnel import and reply); re-running it
needs a fresh `make seed`.

## End-to-end

- Mode B: `make e2e-funnel` — Playwright over the admin CMS (`e2e/cms/test_leads_funnel.py` in emporium), phone
  emulation then desktop, after `make bdd TAGS=@funnel` on a fresh seed.
- Mode C: `make e2e-accept` — AI-tester acceptance run over the CMS.
- Modes, prerequisites and failure triage: the portal guide `guides/leads-end-to-end-testing.md` (entirius-docs).
