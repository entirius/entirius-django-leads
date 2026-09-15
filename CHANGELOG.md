# Changelog

## Unreleased

- **Intel recovery after a toolbox outage.** An analysis that failed transiently (`ToolboxConnectionError`, timeout,
  HTTP 5xx) leaves its claim in `retry`; new beat task `django_leads.retry_failed_analyses` (host schedule every
  10 min) runs it again once `status()` reports the toolbox reachable and the audit is still valid, at most
  `LEADS_INTEL_RETRY_LIMIT` (3) times, then evaluates the `intel_ready` rules. Budget, model and schema failures
  stay failed. The alert fires on the first and the final failure only. Development endpoint
  `test/retry-analyses/`.
- An analysis retry superseded by a newer audit of the domain or a later successful analysis ends `failed/superseded`
  without spending a retry; `retry_failed_analyses` runs once at a time (`QueueOnce`). Draft retries in communicator
  honour the outreach gate (`do_not_contact`, opt-out, anonymised, no legal basis) through `draft_retry_requested`.

## 0.1.0 (unreleased)

Initial release. Leads and B2B pipeline for Volkanos: prospect companies and their contacts in a per-channel
pipeline, rules that decide when outreach may happen, and drafts requested from communicator — never sent by leads
itself. Covers the leads edge cases L-01 … L-19 of the leads-platform catalogue in unit tests, and L-01, L-04,
L-06 … L-12, L-14, L-16, L-17, L-19 in the zeno BDD suite.

- **Pipeline core** — `Channel`, ordered `Stage`s (`open`/`won`/`lost`/`unresponsive`, `on_reply`), `Company`
  deduplicated by `(channel, registrable domain)` (L-01, L-02), `Contact` by normalised email (L-03), an `Activity`
  timeline, and `transition_stage` as the only stage change with the `stage_entered` signal. A stage holding
  companies cannot be deleted (L-18).
- **CSV import** — `leads_import_csv` command, `POST imports/` and a Celery task. The message carries the batch id
  only; the upload waits in `LEADS_IMPORT_TMP_DIR`. Chunks commit with their counters and resume point, ≤ 3 selects
  per chunk (L-05), skipped rows reported by line and reason code without row values (L-04). A sweep every
  10 minutes fails stale batches, deletes old temp files and retries missed form submissions.
- **contact_forms bridge** (soft) — a new form Lead becomes a contact on its company after commit (L-06); consent
  only for an explicit yes, otherwise no legal basis (L-07); nothing happens without a lead creation rule (L-19).
- **Legal basis** — proposed by import and form only when empty (`legal_basis` / `legal_basis_conflict` Activity),
  changed by operators through the API; `consent` always carries a reference.
- **Stage rules** — `stage_entered` / `intel_ready` triggers, a `RuleRun` per evaluation with a claim, cooldown per
  (rule, company) (L-10), event dedupe of redelivered tasks, skips without email (L-08) or hooks (L-11).
- **Outreach gate** — one check inside `request_draft` on locked rows: `do_not_contact` (L-09), no email, opted
  out, anonymised, no legal basis. Drafts only through communicator `communicate()` with review required and an
  agreements legal footer.
- **siteintel analysis** — `report_ready` → one toolbox completion per audit (`AnalysisProfile`): hooks, platform,
  company type, then `intel_ready` rules; prompt values fenced as data.
- **Recipient pick** — primary contact or a toolbox pick among gate-eligible contacts (`RecipientPickProfile`,
  L-12); a pick outside the candidates falls back (L-13).
- **Rotation** — after a finished sequence without a reply, a draft to the next eligible contact; the company parks
  in the `unresponsive` stage after `LEADS_ROTATION_MAX` (L-14); no-draft attempts retry, then give up.
- **communicator receivers** — reply → `on_reply` stage + high notification, reviewer skip → `do_not_contact`,
  sent → timeline.
- **Won seam** — `companies/<id>/create-customer/` links the Customer of a verified email address, routed only with
  django_accounts (L-15).
- **Retention** — daily `anonymise_inactive` pseudonymises contacts of idle companies per channel; `ErasedAddress`
  tokens block re-import; `contact_anonymised` makes communicator suppress the address (L-16).
- **GDPR** — export and erasure by email across every app with `gdpr` hooks: `leads_gdpr` command and
  `api/leads/v2/admin/gdpr/{export,erase}/` (L-17).
- **Connector seam** — `Connector` protocol and `leads_connectors` entry points; `csv` feeds every import, `twenty`
  is an interface only.
- **Admin API v2** — companies (server-side filters `company_type`, `do_not_contact`, `has_reply`), contacts,
  stages, activities, imports, rules, rule runs, analysis and recipient profiles, company communicate /
  request-audit / create-customer, GDPR; development endpoints `test/import-now/`, `test/evaluate/`,
  `test/rotate-now/`, `test/anonymise-now/`.
- **Docs** — `docs/` (`install`, `api`, `concept`, `operations`, `testing`, `gotchas`), `docs/openapi.yaml`, ERD
  config.
- **Fix: CSV header whitespace** — a padded header (e.g. `" domain"`) matched a known column but its values were
  read through the unstripped key and came back empty; header and value lookup now go through the same
  stripped-name mapping.
- **Fix: 409 error codes** — every 409 from the admin API now uses the shared v2 error shape with a distinct
  snake_case `code` (`stage_exists`, `stage_not_empty`, `domain_exists`, `no_stages`, `contact_exists`,
  `profile_exists`, `communicator_channel_missing`, `not_eligible`, `no_draft`, `not_implemented`), replacing the
  ad-hoc `NotEligible` / `NotImplemented` bodies on `communicate/` and `create-customer/`. See `docs/api.md`.
  Requires `entirius-django-utils>=2.1.0` (its v2 handler keeps a 409's code and message); the floor is declared.
