---
title: Testing
description: Which test file covers what, the edge-case IDs, and the BDD and e2e suites in the zeno harness.
---

## Module suite

`make test` — pytest + pytest-django, `tests/settings.py`: `DATABASE_URL` when set (CI, zeno), else sqlite in
memory. The L-05 query ceiling is exact only on PostgreSQL: run `make module-test MODULE=entirius-django-leads` in
zeno. Nothing leaves the process: `ToolboxClient` is patched in the services that call it, and `communicate()`
(with the agreements footer) is patched where a test only needs the call. `ENVIRONMENT = "development"` routes the `test/` endpoints.

| File | Covers |
|---|---|
| `test_normalisers.py` | registrable domain (L-01, L-02, `.test`, no network), email normalisation (L-03) |
| `test_services.py` | upserts fill empty only (L-02, L-03), first stage, whitelists, `transition_stage` timeline + signal on commit only, stage in use (L-18), consent without ref, concurrent form submissions → one company |
| `test_import.py` | report counts and dedup (L-01), skip reasons (L-04), overlong values, file-level failures, resume without replay, no CSV retained, capped report, legal-basis proposal and conflict (L-04), bulk-write fallback, 5 000 rows with a query ceiling (L-05), the command |
| `test_import_transport.py` | id-only task message, temp files deleted, `legacy_message`, sweep of temp files and stale batches, DB down on the final write |
| `test_bridge.py` | form lead → contact after commit (L-06), no consent (L-07), no lead creation rule (L-19), consent string values, free-mail and unknown channel, idempotent retry, broker down, sweep retry and `form_import_failed`, erased address |
| `test_rules.py` | primary draft, no email (L-08), `do_not_contact` before `communicate()` (L-09), cooldown (L-09, L-10), `require_email`, gate refusals of manual communicate (L-13), zero hooks (L-11), request-audit rule, rule errors, missing clause set |
| `test_pipeline.py` | `ai_pick` with stored reason (L-12), invalid pick fallback (L-13), analysis and `intel_ready`, empty sources (L-11), redelivery pays once (L-10), prompt injection, no toolbox call inside a transaction, locking vs opt-out, rotation (L-14), communicator receivers, won seam with and without accounts (L-15) |
| `test_retention.py` | retention selection and anonymisation (L-16), token blocks re-import, GDPR export/erase across modules (L-17), hook registry, API and command, blank email refused, concurrent erase + retention, hash and token parity with the communicator and agreements copies, connector registry |
| `test_admin_api.py` / `test_rules_api.py` | auth (403 for customers), filters, sort allowlist, conflicts, PATCH whitelists, stage delete 409 (L-18), contacts, uploads, consent ref, rules and profiles CRUD, prompt never listed, development endpoints and their 404 |
| `test_openapi.py` | `spectacular --validate --fail-on-warn` over `django_leads.urls` |

Every leads edge case L-01 … L-19 has at least one unit test.

## BDD in zeno

Emporium test package, `features/leads/` (read-only from this repo). Needs `make dev`, `make seed` (`SEED OK`),
`make mail` and the AI toolbox up (`make toolbox-check`).

| Feature | IDs | One-shot |
|---|---|---|
| `leads_import.feature` | L-01, L-04, L-06, L-07, L-19 | no |
| `leads_pipeline.feature` | L-08, L-09, L-10, L-11, L-12, L-14 | `@leads-oneshot` on L-14 |
| `leads_gdpr.feature` | L-16, L-17 | `@leads-oneshot` on both |
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
