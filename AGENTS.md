# AGENTS.md

Leads and B2B pipeline for the Volkanos platform: companies, contacts, stages, rules and recipient selection — distribution `entirius-django-leads`, Django app `django_leads`.

## Commands

| Command | Meaning |
|---|---|
| `make install` | sync dependencies (uv, incl. extras) |
| `make check` | lint + format-check (ruff) |
| `make fix` | auto-fix lint + format |
| `make test` | test suite (pytest + pytest-django) |
| `make install` needs the sibling clones `../entirius-django-{agreements,contact-forms,communicator,siteintel,notifications,utils}` | `[tool.uv.sources]` until their releases |

## Conventions

- English only: code, docs, commits, branches, PRs.
- MPL-2.0: every non-trivial source file carries the license header (pre-commit inserts it).
- Toolchain: uv + ruff + hatchling + pytest; all config in `pyproject.toml`; `uv.lock` committed.
- Git flow: `master` (production) + `develop` (integration); changes land via PR; semver tag on `master`.
- Never rename the package / Django app_label / DB table prefix `django_leads` — it is a schema contract.
- Migrations are part of the public contract — never edit an already released migration.
- Default: do not commit — git is the user's call.

## Commit Message Format

**NEVER add `Co-Authored-By: Claude ...` (or any other Claude/Anthropic attribution) to commit messages.**

This overrides the default Claude Code behavior of appending a `Co-Authored-By` trailer. Commit messages MUST contain only the user's authored content — no robot footer, no "Generated with Claude Code" line, no co-author trailer.

Same rule applies to PR descriptions: no `Generated with [Claude Code]` footer.

## Architecture

- Models: `Channel` (+ `retention_days`), `Stage` (ordered per channel, `on_reply`), `Company` (unique
  `(channel, domain)`), `Contact` (unique `(company, email)` for non-empty emails, `legal_basis` null = none),
  `Activity` (timeline, written only by `activity_service`), `ImportBatch` (counts, `size_bytes`, `row_count`,
  `last_row_done`, report of skipped rows `{row, reason}` — never the CSV itself).
- Dedup: `utils/domains.registrable_domain` (tldextract bundled PSL, never the network; `.test` added) and
  `utils/emails.normalize_email`. A match fills empty fields only; a contact without email matches by name.
  `upsert_company`/`upsert_contact` are race-safe (insert in a savepoint, re-read on `IntegrityError`) and write
  no Activity — callers record their own. PSL suffixes such as `shop.pl` are not registrable.
- Legal basis (GDPR): never filled like other fields. Import/form call `contact_service.propose_legal_basis` —
  sets it only when empty (Activity `legal_basis` with from/to/source/`consent_ref`), a different recorded basis
  stays and is logged as `legal_basis_conflict`. Operators change it via `set_legal_basis` (API PATCH). `consent`
  without a `consent_ref` (`form:<lead id>`, `import:<batch>:<row>`, `admin:<user>:<consent_ref>`) raises
  `ConsentRefRequired`. The admin API requires `consent_ref` (non-blank, ≤ 255) when it sets `consent` (400
  otherwise); other bases are recorded as `admin:<user>`.
- CSV import: entry points `manage.py leads_import_csv --channel X --file F [--sync]` and `POST imports/`. The
  upload is written by the caller to `LEADS_IMPORT_TMP_DIR/<batch_id>.csv` (`import_service.store_upload`: file
  0600, directory 0700) before `tasks.enqueue_import(batch_id)` (task `django_leads.import_csv`, queue
  `leads_default`) — the message carries the batch id only, no CSV row passes through the broker. The task deletes
  the file once the batch is done or failed (kept across an `OperationalError` retry); a missing file fails the
  batch `missing_file`, a message from before this contract (id + content) fails it `legacy_message` unprocessed.
  When marking failed after the last retry hits the DB again, the task logs batch id + code and returns — the sweep
  below ends the batch.
  Chunks of `LEADS_IMPORT_CHUNK_SIZE`, ≤ 2 selects + bulk writes each, one `transaction.atomic()` per chunk
  together with the counters and `last_row_done` — a retry resumes after it, nothing is replayed. A bulk write the
  DB rejects is redone row by row in savepoints. Skip reasons: `no_domain_no_email`, `freemail_no_domain`,
  `invalid_legal_basis`, `invalid_domain`, `invalid_email`, `too_long:<field>`, `conflict`, `invalid_value`.
  Any other error ends the batch `failed` with report entry `{row: 0, reason: <code>}` (`missing_header`,
  `csv_error`, `encoding_error`, `no_stages`, `database_error` after the last retry, `internal_error`,
  `missing_file`, `legacy_message`, `stale`) and never raises; only `OperationalError` propagates to the task retry. The report keeps at most
  `LEADS_IMPORT_REPORT_MAX` (1 000) skipped rows, no row values.
- Sweep (`services/sweep_service`, task `django_leads.fail_stale_import_batches`, queue `leads_default`, beat
  every 10 min — host schedule): temp CSV files older than 24 h deleted (file mtime, no DB needed); `pending` /
  `running` batches without progress for `LEADS_IMPORT_STALE_MINUTES` (30) → `failed` (`stale`) + file deleted;
  contact_forms Leads of the last 24 h older than that cutoff with neither a `form` nor a `form_import_failed`
  Activity carrying their `lead_id` are imported once more — a failure records `form_import_failed` on the company
  of the submission's domain when it exists (otherwise only logged). Submissions skipped by design (free-mail, no
  channel) are re-tried by every sweep inside those 24 h at no effect.
- Stages: `stage_service.transition_stage` is the only stage change (timeline + `signals.stage_entered` on commit);
  `delete_stage` raises `StageInUse` (API 409). `signals.contact_anonymised` is emitted by plan 11.
- contact_forms bridge (`signals/contact_forms_bridge.py`, soft): on Lead creation, after commit, upserts
  Company + Contact in `transaction.atomic()` (consent only for `True`, `1` or `true/1/yes/on/y/tak` in a
  `LEADS_FORM_CONSENT_KEYS` key; Activity "no legal basis" when the contact ends without one). Writes FORM (and
  legal-basis) activities only. An `OperationalError` queues task `django_leads.import_form_lead` (idempotent by
  the lead id); an unreachable broker is logged (lead id + error class) and left to the sweep; other failures are
  logged, never raised. Never writes `django_contact_forms.Lead`.
- Admin API v2 (`JWTAuthentication` + `IsAdminUser`): `api/leads/v2/admin/<channel_idx>/` — `companies/`
  (`?stage=&search=&sort=`), `companies/<id>/`, `companies/<id>/transition/`, `contacts/`, `stages/`,
  `activities/?company=`, `imports/`, `rules/`, `rule-runs/?company=`, `analysis-profiles/`, `recipient-profiles/`,
  `companies/<id>/{communicate,request-audit,create-customer}/`; development `test/import-now/` (multipart
  upload imported in the request — no worker), `test/evaluate/`, `test/rotate-now/`.
  PATCH whitelists live in the services.
- Outreach gate (`recipient_service.block_reason` / `eligible`): email, `legal_basis` set, no `opt_out_at`, no
  `anonymised_at`, company not `do_not_contact`. `outreach_service.request_draft` re-reads contact and company and
  checks it right before `communicate()` on every path (rules, rotation, manual); a refusal is Activity
  `blocked: <reason>` (`do_not_contact`, `no_email`, `opted_out`, `anonymised`, `no_legal_basis`), never a draft.
- Rules (`services/rule_service.evaluate_rules(company, trigger, *, stage=None, event="")`): active `StageRule`s of
  the trigger (and stage) by `order`. Phase (a) under the company row lock, on the re-read row: a `RuleRun` of
  (rule, company, `event`) already exists → returned, nothing runs again (`event` = `stage:<id>:<entered_at>` from
  the `stage_entered` receiver, `audit:<id>` from intel; empty = no dedupe, e.g. `test/evaluate/`);
  `do_not_contact` → blocked; a `fired` or in-flight `claimed` run inside `cooldown_hours` → cooldown (skipped,
  blocked and cooldown runs never count); `request_audit` → siteintel; `require_hooks` without hooks → skipped;
  `require_email` without a candidate → skipped (`false` lets the rule through, the gate then blocks `no_email`);
  else the run is written `claimed` and committed. Phase (b) outside any transaction: recipient pick (toolbox), rule
  `require_legal_basis`, `request_draft`. Phase (c) completes the run (`done`). A `claimed` run older than
  `LEADS_CLAIM_STALE_MINUTES` found by a redelivery → `failed` (`outcome_unknown`), never retried. Errors become
  Activity `rule error: <class>` and a skipped run, never raise.
- Claims (`models.Claim`, `services/claim_service`): the same pattern for paid calls outside rules — key
  `intel:<audit>:<task id>` and `rotation:<thread>`; `claimed` → `done` / `retry` / `failed`.
- Drafts: `outreach_service.request_draft` is the only caller of communicator `communicate()` (leads never writes a
  `Message`); footer from agreements `resolve_clause_set` (contact language → channel default), missing clause set
  → Activity `skipped: no clause set`. Manual path `POST companies/<id>/communicate/` skips rule conditions only —
  the gate answers 409 `NotEligible` with the reason.
- Intel: `report_ready` → task `django_leads.analyse_intel` → claim → one toolbox completion (`AnalysisProfile`
  `leads.analysis`, never retried; a redelivered message keeps its task id and finds the claim) → hooks/platform/type
  → `intel_ready` rules. Empty sources → no toolbox call, `company.hooks` cleared, Activity `intel_empty`, then the
  rules (never on stale hooks). Failures → Activity `analysis failed: <code>` + notification (`LEADS_NOTIFY_ROLE`,
  medium).
- Prompts (`utils/prompts`): the system message is the static `DATA_INSTRUCTIONS`; the profile template is rendered
  into the user message in one pass (a value is never expanded again), every lead/company/site value wrapped in
  `<company_data>…</company_data>` with the delimiter stripped from the value.
- Recipient pick (`ai_pick`): candidates `{"candidates": [...]}` JSON in the last user message
  (`RecipientPickProfile` `leads.pick_recipient`); an id outside the candidates falls back to the primary contact.
- Rotation: `sequence_finished` → task `django_leads.rotate_thread`, daily beat `django_leads.rotate_unresponsive`
  (host schedule; each thread isolated — an error is logged by class, the scan continues; `test/rotate-now/` scans
  its channel only). Claim `rotation:<thread>` under the company row lock: another rotation of the company in flight
  → nothing now; `do_not_contact` → Activity `blocked` + claim `done`; `LEADS_ROTATION_MAX` reached or no next
  gate-eligible contact → stage `kind=unresponsive` + claim `done`; missing stage → `ConfigurationError`, rolled back,
  Activity `no unresponsive stage` written outside, no claim (the thread rotates once the stage exists). Otherwise the
  draft is requested outside the lock; only a draft raises `rotation_count` (`F()`) and writes Activity `rotation`.
  No draft → Activity `rotation_failed`, count unchanged, claim `retry` (next attempt after
  `LEADS_ROTATION_RETRY_HOURS`), after `LEADS_ROTATION_MAX_FAILURES` → `rotation_gave_up`, claim `failed`.
- Customer link: `EmailAddress` matched case-insensitively with `verified=True` → user → `Customer`.
- Prompts (`prompt_text`, rendered prompts) never reach logs, Activity data or API lists.
- Hard deps: utils, regional, agreements (`LegalBasis`), communicator, siteintel; soft: `django_contact_forms`,
  `django_notifications` (alerts), `django_accounts` (`companies/<id>/create-customer/` routed only when installed).

## Signals

| Direction | Signal | Handling |
|---|---|---|
| emitted | `django_leads.signals.stage_entered(company, stage)` | own receiver → task `django_leads.evaluate_rules` |
| emitted | `django_leads.signals.contact_anonymised(contact)` | plan 11 |
| consumed | siteintel `report_ready(audit, succeeded_sources)` | task `django_leads.analyse_intel` |
| consumed | communicator `reply_received(thread, reply)` | Activity `reply`, `on_reply` stage, high notification |
| consumed | communicator `company_skipped(subject_ref)` | `do_not_contact = True`, Activity `blocked` |
| consumed | communicator `message_sent(message)` | Activity `sent` |
| consumed | communicator `sequence_finished(thread)` | task `django_leads.rotate_thread` |

All receivers: `dispatch_uid="django_leads.<name>"`, run after commit, never raise into the sender; only
`subject_ref` `leads.Company:<int>` is handled.

## Host integration

- Service and worker MUST share `LEADS_IMPORT_TMP_DIR` (default `<system temp>/django_leads`): in separate
  containers mount one named volume at that path in both (zeno: plan 12). Without it every queued import fails
  `missing_file`; `test/import-now/` and `leads_import_csv --sync` need no worker.
- Beat: `django_leads.fail_stale_import_batches` every 10 min, `django_leads.rotate_unresponsive` daily.

## Settings

`LEADS_QUEUE_DEFAULT` (`leads_default`), `LEADS_IMPORT_*` (`CHUNK_SIZE`, `TMP_DIR`, `STALE_MINUTES`,
`REPORT_MAX`), `LEADS_FORM_*`, `LEADS_FREEMAIL_DOMAINS`, `LEADS_ROTATION_MAX` (2), `LEADS_ROTATION_RETRY_HOURS` (24), `LEADS_ROTATION_MAX_FAILURES` (3),
`LEADS_CLAIM_STALE_MINUTES` (30), `LEADS_NOTIFY_ROLE`
(`sales_admin`), `LEADS_ANALYSIS_MAX_HOOKS` (10); toolbox `AI_TOOLBOX_*` (django_utils); development endpoints need `ENVIRONMENT = "development"`.

## Testing end-to-end

- Unit (`make test`, sqlite; `make module-test MODULE=entirius-django-leads` in zeno, PostgreSQL — the L-05
  query ceiling is exact only there): L-01, L-02, L-03, L-04, L-05, L-06, L-07, L-08…L-15, L-18, L-19.
- BDD (`make bdd TAGS=@leads`): `features/leads/leads_import.feature` L-01, L-04, L-06, L-07, L-19 (not one-shot);
  `features/leads/leads_pipeline.feature` L-08…L-12, L-14 (`@leads-oneshot` — needs a fresh `make seed`). The funnel walkthrough is the E2E guide of plan 12.
