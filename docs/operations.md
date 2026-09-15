---
title: Operations
description: Day 2 — CSV import and its report, the sweep, GDPR export and erasure, retention, running tasks by hand, monitoring.
---

Install-time facts (apps, settings, queue, beat) are in `install.md`. This page starts after the first channel has
stages.

## Management commands

| Command | Flags | What it does |
|---|---|---|
| `manage.py leads_import_csv` | `--channel <idx>` (required), `--file <path>` (required), `--sync` | Creates a batch (`created_by` = `manage.py`). Without `--sync`: stores the file in `LEADS_IMPORT_TMP_DIR` and queues `django_leads.import_csv` (prints `batch N queued`). With `--sync`: imports in the process and prints `batch N <status>: created= matched= skipped=`. Unknown channel → `CommandError`. |
| `manage.py leads_gdpr` | `--email X` (required) and exactly one of `--export PATH` / `--erase`; `--yes` | Export writes the JSON to `PATH` with mode 0600 and prints the modules. Erase prints one line of counts per module. `--email` is normalised and validated (`CommandError` on blank or malformed). The erase confirmation is asked only on a TTY; without one `--yes` is required. |

## Import

### CSV format

UTF-8 (a BOM is accepted), header row required — the batch fails `missing_header` when no header matches a known
column after stripping surrounding whitespace (`" domain "` matches `domain`); values are then read through that
same stripped mapping, so a padded header never imports empty values. Columns (all optional, unknown ones ignored):
`company_name`, `domain`, `website`, `company_type`,
`industry`, `first_name`, `last_name`, `email`, `job_title`, `language` (ISO 639-1), `legal_basis` (`consent`,
`legitimate_interest`, `contract`), `phone`.

- The company domain is `domain`, else `website`, else the email host; a free-mail host without `domain`/`website`
  is skipped.
- A row without email and without a name creates or matches only the company.
- `company_type` outside `MANUFACTURER`/`WHOLESALE`/`RETAILER`/`UNKNOWN` is ignored, not a skip.
- An email whose token is in `ErasedAddress` skips the whole row — no company, no contact.

### How a run works

The API writes the upload to `LEADS_IMPORT_TMP_DIR/<batch_id>.csv` and queues a message carrying the batch id only —
no CSV row passes through the broker. Rows are applied in chunks of `LEADS_IMPORT_CHUNK_SIZE`: ≤ 3 selects (erased
addresses, companies, contacts) and bulk writes per chunk, committed in one transaction together with the counters
and `last_row_done`. A retry (`OperationalError`, 3 retries with backoff) resumes after the last committed row. A bulk
write the database rejects is redone row by row in savepoints. The file is deleted once the batch is `done` or
`failed`, and kept across a retry.

### Report codes

Skipped rows (`{row, reason}`, at most `LEADS_IMPORT_REPORT_MAX`):

| Reason | Meaning |
|---|---|
| `no_domain_no_email` | neither domain, website nor email |
| `freemail_no_domain` | email on a `LEADS_FREEMAIL_DOMAINS` host, no domain or website |
| `invalid_domain` / `invalid_email` | no registrable domain / email fails validation |
| `invalid_legal_basis` | value outside the `LegalBasis` choices |
| `too_long:<field>` | a value longer than its column |
| `erased_address` | the address was erased or anonymised |
| `conflict` / `invalid_value` | the database rejected the row alone (integrity / data error) |

File-level failures end the batch `failed` with `{row: 0, reason}` and never raise: `missing_header`, `csv_error`,
`encoding_error`, `no_stages`, `database_error` (after the last retry), `internal_error`, `missing_file` (the worker
cannot see the upload — shared temp dir missing), `legacy_message` (a message from before the id-only contract),
`stale` (the sweep).

### The sweep

Task `django_leads.fail_stale_import_batches` (beat every 10 minutes) returns `{tmp_files, batches, form_leads}`:

1. Temp CSV files older than 24 h are deleted (file mtime, no database needed).
2. `pending`/`running` batches without progress for `LEADS_IMPORT_STALE_MINUTES` → `failed` (`stale`), file deleted.
   This is also what ends a batch whose final `failed` write hit a database that was still down.
3. contact_forms submissions of the last 24 h, older than that cutoff, with neither a `form` nor a
   `form_import_failed` Activity carrying their `lead_id`, are imported once more. A failure records
   `form_import_failed` on the company of the submission's domain when it exists, otherwise it is only logged.
   Submissions skipped by design (free-mail, unknown channel, erased address) are re-tried by every sweep inside
   those 24 h at no effect.

## GDPR

| Request | Command | API |
|---|---|---|
| Export (art. 15) | `manage.py leads_gdpr --email X --export out.json` | `POST api/leads/v2/admin/gdpr/export/` `{email}` |
| Erasure (art. 17) | `manage.py leads_gdpr --email X --erase [--yes]` | `POST api/leads/v2/admin/gdpr/erase/` `{email}` |

Export: `{email, generated_at, modules}` from every installed app with a `gdpr` module (leads: `Contact`,
`Company`, `Activity`). Erasure is irreversible and runs in one transaction: a `note` "gdpr erase requested" on every
affected company (actor = user or `manage.py`), then every module's hook. Leads remembers the token even when no
contact matches (so a later import or form cannot re-create the address), anonymises the contacts and scrubs their
Activities. After commit `contact_anonymised` makes communicator suppress the token on every channel.

A blank address is refused everywhere — it would match every contact without an email.

## Retention

Task `django_leads.anonymise_inactive` (daily, `QueueOnce`) returns anonymised contacts per channel idx. A contact is
selected when it is not anonymised yet, its company's `last_activity_at` is older than `Channel.retention_days`
(null → `LEADS_RETENTION_DAYS`) — a company with no Activity at all is never picked — the company is not in a `won`
stage, and the contact has no Activity of its own after the cutoff. Shorten or lengthen per channel with
`retention_days` in Django admin.

## Running tasks by hand

| Need | Production shell | Development (`ENVIRONMENT=development`) |
|---|---|---|
| Import without a worker | `leads_import_csv --sync` | `POST test/import-now/` |
| Evaluate rules now | `rule_service.evaluate_rules(company, trigger, stage=…)` | `POST test/evaluate/` |
| Rotation scan | `tasks.rotate_unresponsive.delay()` | `POST test/rotate-now/` (this channel) |
| Retention at a given clock | `tasks.anonymise_inactive.delay(as_of="<ISO>")` | `POST test/anonymise-now/` `{as_of}` (every channel) |
| Sweep | `tasks.fail_stale_import_batches.delay()` | — |

Rules and rotation request real drafts; with `ai_pick` or an analysis they call the toolbox and spend its budget.

## Monitoring

| Signal | Where | Meaning |
|---|---|---|
| `ImportBatch.status = failed`, `report[row=0].reason` | `GET imports/<id>/`, Django admin | file-level failure; `missing_file` means service and worker do not share `LEADS_IMPORT_TMP_DIR` |
| Activity `analysis failed: <code>` + medium notification | company timeline, notifications | toolbox error (`code`), `no_profile`, `schema` (answer without `hooks` list / `platform` string) |
| Activity `rule error: <class>`, run `skipped` | timeline, `rule-runs/` | unexpected exception in a rule; details in the log (`leads: rule … failed`) |
| run `detail = outcome_unknown` | `rule-runs/` | a worker died mid-call; never retried — decide by hand |
| Activity `skipped: no clause set` / `skipped: no legal basis` | timeline | agreements has no clause set for the basis and language |
| Activity `no unresponsive stage` + log `channel … has no unresponsive stage` | timeline, log | add a stage with `kind=unresponsive`; the thread rotates on the next scan |
| Activity `rotation_gave_up` | timeline | `LEADS_ROTATION_MAX_FAILURES` attempts without a draft |
| Activity `legal_basis_conflict` | timeline | an import or form proposed a different basis than the recorded one; resolve with PATCH |
| Log `django_notifications not installed — leads alerts are logged only` | log, once per process | replies and failures raise no notification |
| Log `leads: receiver … failed` | log | a signal receiver raised; the sender was not affected |

Prompts and rendered prompts never reach logs, Activity data or API lists.
