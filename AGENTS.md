# AGENTS.md

Leads and B2B pipeline for the Volkanos platform: companies, contacts, stages, rules and recipient selection — distribution `entirius-django-leads`, Django app `django_leads`.

## Commands

| Command | Meaning |
|---|---|
| `make install` | sync dependencies (uv, incl. extras) |
| `make check` | lint + format-check (ruff) |
| `make fix` | auto-fix lint + format |
| `make test` | test suite (pytest + pytest-django) |
| `make install` needs the sibling clones `../entirius-django-agreements` and `../entirius-django-contact-forms` | `[tool.uv.sources]` until their releases |

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
  without a `consent_ref` (`form:<lead id>`, `import:<batch>:<row>`, `admin:<user>`) raises `ConsentRefRequired`.
- CSV import: entry points `manage.py leads_import_csv --channel X --file F [--sync]` and `POST imports/` →
  `tasks.enqueue_import` (task `django_leads.import_csv`, queue `leads_default`; the CSV travels in the message —
  service and worker share no disk — with a masked `argsrepr`). `import_service.run_content` writes it to a temp
  file in `LEADS_IMPORT_TMP_DIR` (default system temp) and deletes it in `finally`.
  Chunks of `LEADS_IMPORT_CHUNK_SIZE`, ≤ 2 selects + bulk writes each, one `transaction.atomic()` per chunk
  together with the counters and `last_row_done` — a retry resumes after it, nothing is replayed. A bulk write the
  DB rejects is redone row by row in savepoints. Skip reasons: `no_domain_no_email`, `freemail_no_domain`,
  `invalid_legal_basis`, `invalid_domain`, `invalid_email`, `too_long:<field>`, `conflict`, `invalid_value`.
  Any other error ends the batch `failed` with report entry `{row: 0, reason: <code>}` (`missing_header`,
  `csv_error`, `encoding_error`, `no_stages`, `database_error` after the last retry, `internal_error`) and never
  raises; only `OperationalError` propagates to the task retry. The report keeps at most
  `LEADS_IMPORT_REPORT_MAX` (1 000) skipped rows, no row values.
- Stages: `stage_service.transition_stage` is the only stage change (timeline + `signals.stage_entered` on commit);
  `delete_stage` raises `StageInUse` (API 409). `signals.contact_anonymised` is emitted by plan 11.
- contact_forms bridge (`signals/contact_forms_bridge.py`, soft): on Lead creation, after commit, upserts
  Company + Contact in `transaction.atomic()` (consent only for `True`, `1` or `true/1/yes/on/y/tak` in a
  `LEADS_FORM_CONSENT_KEYS` key; Activity "no legal basis" when the contact ends without one). Writes FORM (and
  legal-basis) activities only. An `OperationalError` queues task `django_leads.import_form_lead` (idempotent by
  the lead id); other failures are logged, never raised. Never writes `django_contact_forms.Lead`.
- Admin API v2 (`JWTAuthentication` + `IsAdminUser`): `api/leads/v2/admin/<channel_idx>/` — `companies/`
  (`?stage=&search=&sort=`), `companies/<id>/`, `companies/<id>/transition/`, `contacts/`, `stages/`,
  `activities/?company=`, `imports/`. PATCH whitelists live in the services.
- Hard deps: utils, regional, agreements (`LegalBasis`); soft: `django_contact_forms`.

## Testing end-to-end

- Unit (`make test`, sqlite; `make module-test MODULE=entirius-django-leads` in zeno, PostgreSQL — the L-05
  query ceiling is exact only there): L-01, L-02, L-03, L-04, L-05, L-06, L-07, L-18, L-19.
- BDD (emporium `features/leads/leads_import.feature`, `make bdd TAGS=@leads`): L-01, L-04, L-06, L-07, L-19;
  not one-shot. The funnel walkthrough is the E2E guide of plan 12.
