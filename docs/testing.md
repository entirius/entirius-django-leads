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
| `test_rules.py` | primary draft, no email, `do_not_contact` before `communicate()`, cooldown, `require_email`, gate refusals of manual communicate, zero hooks, request-audit rule, rule errors, missing clause set, toolbox switched off (manual communicate 503, rule `skipped` without cooldown) |
| `test_pipeline.py` | `ai_pick` with stored reason, invalid pick fallback, analysis and `intel_ready`, empty sources, redelivery pays once, prompt injection, no toolbox call inside a transaction, locking vs opt-out, rotation, communicator receivers, won seam with and without accounts, intel retry after a toolbox outage (transient vs permanent, attempts cap, unreachable toolbox, expired audit, taken claim, no call inside a transaction, dev endpoint), toolbox switched off (analysis skipped then run after enabling, pick fallback, rotation waits in `retry`) |
| `test_retention.py` | retention selection and anonymisation, token blocks re-import, GDPR export/erase across modules, hook registry, API and command, blank email refused, concurrent erase + retention, hash and token parity with the communicator and agreements copies, connector registry |
| `test_admin_api.py` / `test_rules_api.py` | auth (403 for customers), filters, sort allowlist, conflicts, PATCH whitelists, stage delete 409, contacts (remove = delete or anonymise, one primary), uploads, consent ref, rules and profiles CRUD, prompt never listed, development endpoints and their 404 |
| `test_stage_events.py` | event → stage map: `reply:<class>` → `reply` fallback, null target, no row, won / lost and forward-only guards (table), `first_sent` (first send only, never backwards), replies by class and alert severity, `sequence_finished` then rotation, API, `on_reply` data migration and its reverse |
| `test_do_not_contact.py` | `opt_out` and `set_do_not_contact` with communicator suppressions, confirmed opt-out under `channel` and `install` scope, reviewer skip, hard bounce, wrong-person reply, gate reasons in order with `can_mail`, `mailable` endpoint |
| `test_mail_block.py` | `mail_block` of every reason of the send check equal to the first reason of `mailable/`, on the list, the company card and one contact; without a communicator channel; the same query count for any page size; `company_name` on a rule run in one query for the page, runs of a deleted company gone |
| `test_import_rules.py` | `run_rules`: one evaluation per created company, none without the flag, none extra on a resumed run, rolled-back bulk write, drafts for created companies, upload field, batch report, command; `origin` column and filter |
| `test_attributes.py` | every attribute kind valid / refused / null removes, required on create and patch, definition rules (built-in key, options, kind fixed once used, delete in use), attributes API, company and contact values, list filter with a query budget and the GIN index (PostgreSQL), `fields/`, facts, GDPR export / anonymise / erase of contact attributes |
| `test_conditions.py` | every op against text, number, select, multiselect, bool, date and attribute facts (table), missing values, validation at save, a failing condition skips without cooldown, CSV-imported select drives a rule for matching companies only, template context `attr` / `contact_attr` with a missing key rendering empty |
| `test_scoring.py` | threshold boundaries, sum of matching rules, disqualifier → S4, profile validation and CRUD, `score/` stores the result and the timeline reasons, 409 without a profile, rule action `score`, `segment` filter and `score_points` sort within a query budget |
| `test_owner.py` | owner picker (active staff only, service users left out, search, cap of 50, `{id, username, name}` only), manual create defaults to the caller, patch set / clear with `owner:` notes, a non-staff or inactive owner refused, bulk assign (only changes, over 200 or a foreign company → 400), list filter `me` / `none` / id, `owner__username` sort, constant list query count (50 companies, 5 owners), import with and without `owner_id` (bulk and slow path), reply alert to the owner only / to the role, no-op without notifications, `mailbox_user_id` of drafts |
| `test_import_attributes.py` | two attribute columns and a contact column, ignored headers reported once, bad value skips the row, matched company filled for missing keys only |
| `test_funnel.py` | funnel numbers on a fixed timeline, origin and date filters, fixed query count, endpoint |
| `test_deals.py` | deal stages (order, unique key, delete / kind refused while in use), opening (first open stage, owner and currency defaults, timeline), amounts (string round trip, floats, decimals, digits, negative, no currency), update whitelist and contact of the company, open → won → reopened → lost with the signal, lost reason 400, no lost stage 409, delete only open, a deleted contact keeps the deal, list filters and sort, board order / per-currency totals / column limit / owner, query counts of list and board, company card counts and deals, deal timeline, deal attributes and `fields/?entity=deal`, default currency, deal tasks on the timeline and `open_tasks` |
| `test_activity_words.py` | `subject_label` = company name on drafts and hand-written mail; reply alert title in words and `REPLY_ALERTED` (notifications installed / absent, a company of no one); activity `data` of the CRM features (reply, tasks incl. reopened, composed, sequence stopped / paused, deals, owners, bulk assign); import `ignored_columns` and `automation_started_count` |
| `test_version.py` | `__version__` equals the `pyproject.toml` version |
| `test_tasks.py` | a task per reply class (table), the interested task's note, no task for the other classes, owner and due now, dedup, `resume_days`, a newer reply / opt-out / anonymisation cancelling the "come back" task, no-op without `django_todo`, a failing creation keeping the reply, timeline of person-created, done and cancelled tasks, other subjects and channels ignored, channel settings API, GDPR export and scrub (skipped without `django_todo`) |
| `test_compose.py` | compose through the owner's mailbox after the gate, an opted-out contact refused with one `blocked`, answering a reply, a reply of another company or contact, no communicator channel, no clause set, bad requests, staff only |
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
