# Changelog

## Unreleased

- **Company owner.** `Company.owner` (FK user, `SET_NULL`, index `(channel, owner)`) and `ImportBatch.owner`
  (migration `0012`) — the module's first user FK, so it now reads `AUTH_USER_MODEL`. Written only by
  `owner_service` (never in `EDITABLE_FIELDS`), every change an Activity `note` "owner: <username | none>". API:
  `GET assignable-users/?search=` (active staff by name, ≤ 50, `{id, username, name}` only; service users of access
  applications left out), `owner_id` on company create (default: the requesting user when assignable) and patch (null clears),
  `POST companies/assign/` `{company_ids (≤ 200), owner_id | null}` → `{updated}`; company list filter
  `owner=me|none|<id>`, sort `owner__username` (unowned last either way), response `owner {id, username, name} | null` (joined, no N+1).
  Import: multipart `owner_id` → created companies and matched ones without an owner.
- **Alerts and drafts follow the owner.** `alert_service.alert` passes the owner as `recipient_user_id` (the
  owner only; unowned or a deactivated owner = the role as before) — needs entirius-django-notifications 0.3.0; `request_draft` (rules,
  rotation, manual) passes `mailbox_user_id=company.owner_id` to `communicate()`. Raise the notifications pin to
  `>=0.3.0` with the communicator one at release.

- **Custom attributes.** `AttributeDefinition(channel, entity, key, label, kind, options, required, order,
  show_in_list, is_active)`, entity `company | contact | deal`, kind `text | number | bool | date | select |
  multiselect | url`; values in `Company.attributes` / `Contact.attributes` (JSON), validated and normalised only by
  `attribute_service`. A key may not shadow a built-in fact; the kind is fixed once a value is stored; a definition
  in use is deactivated, not deleted. API `attributes/` (CRUD, area `leads.settings`); company and contact create /
  patch take `attributes` (patch merges, `null` removes a key, `required` enforced on create and on a patch touching
  the key). Company list filter `attr.<key>=<value>` for `show_in_list` select / bool / text attributes, served by a
  GIN index on `Company.attributes` (migration `0011`).
- **One accessor of facts.** `fact_service.facts(company, contact=None)` (built-ins plus `attr.<key>` and
  `contact.attr.<key>`) feeds rule conditions, scoring and the template context; `GET fields/` describes them with
  kinds and options.
- **Rule conditions.** `StageRule.conditions` = `[{field, op, value}]`, ops `eq`, `ne`, `in`, `contains`, `gte`,
  `lte`, `empty`, all must hold; checked at save against `fields/`. Evaluated after the cooldown and before the
  action: a failing condition is a `skipped` run with Activity `skipped: condition <field>` and starts no cooldown.
  `require_hooks` / `require_email` stay.
- **Scoring.** `ScoringProfile(channel, key, thresholds, rules, disqualifiers, is_active)`; points ≥ L → `S1`, ≥ M →
  `S2`, ≥ S → `S3`, else `S4`, a disqualifier forces `S4`. The result is stored on the company (`score_points`,
  `score_segment`, `scored_at`) and written to the timeline (Activity `score`, reasons as text and data). API
  `scoring-profiles/` (CRUD, area `leads.settings`), `POST companies/<id>/score/` (409 `NO_SCORING_PROFILE`), rule
  action `score`, company list filter `segment` and sort `score_points` (never-scored last).
- Templates: the draft context adds `attr` and `contact_attr` (`{attr[key]}`; a key without a value renders empty).
- Import: `attr.<key>` / `contact.attr.<key>` columns for active definitions (a bad value skips the row with
  `bad_attribute:<key>`, a matched row gets missing keys only); every column the import does not read is reported
  once as `{row: 1, reason: "ignored_column:<header>"}`. `CandidateRow` gains `attributes` / `contact_attributes`.
- GDPR: contact attributes are personal data — exported with the contact, cleared by anonymisation and erasure.

- **Release order:** this release needs entirius-django-communicator 0.5.0 (reply classes and `plan_applied`,
  `message_bounced`, `suppression_service.can_mail` / `explain` / `scope`). Raise the `entirius-django-communicator`
  pin to `>=0.5.0` when releasing this module, once 0.5.0 is on PyPI — leads imports that API directly.
- **The event → stage map replaces `Stage.on_reply`** (breaking for API clients). `StageEventRule(channel, event,
  target_stage, forward_only)`; events `first_sent`, `reply`, `reply:<class>`, `sequence_finished`. `reply:<class>`
  falls back to `reply`, a null target keeps the stage, no row moves nothing; a move never leaves a `won` / `lost`
  stage and, with `forward_only` (default), only goes to a higher `order`. Migrations `0008`–`0010`: every
  `on_reply` stage becomes a `reply` row (`forward_only=false`, the old behaviour) plus a `reply:out_of_office` row
  that keeps the stage; the reverse restores the flag. `on_reply` is gone from stage requests and responses. API
  `stage-events/` (list, `PUT`, `DELETE`; area `leads.settings`).
- Replies: Activity `reply` carries the class (`reply received: <class>`), the alert takes the reply plan's
  severity (`none` = no alert). A `wrong_person` reply sets `Contact.wrong_person_at` (Activity `note`); the gate,
  the recipient pick and the rotation skip that contact. A finished sequence may move the stage before the rotation.
- **Do not contact has one writer each.** `contact_service.opt_out` sets `opt_out_at` and suppresses the address in
  communicator; `company_service.set_do_not_contact` (also behind `PATCH companies/<id>/` and the reviewer's skip)
  suppresses the company's domain and removes it when turned off. A confirmed opt-out from communicator opts out
  the contacts with the address in its channel, or in every channel under the `install` suppression scope. A hard
  bounce leaves Activity `note` "address invalid: hard bounce".
- Gate: `recipient_service.block_reasons` lists every refusal (`do_not_contact`, `no_email`, `opted_out`,
  `anonymised`, `wrong_person`, `no_legal_basis`, then communicator's `suppressed` / `address_invalid`).
  `GET companies/<id>/contacts/<contact_id>/mailable/` explains it with the legal basis, communicator's answer and
  the retention clock.
- **Import `run_rules`** (multipart field, default off; `leads_import_csv --run-rules`): every created company
  enters its first stage with `stage_entered` after its chunk commits — one `evaluate_rules` task each, shown as
  `rules_queued` on the batch. `ImportBatch.run_rules`.
- `Company.origin` (free text, ≤ 64): CSV column `origin` (fills an empty origin), editable, list filter `origin`.
  `GET reports/funnel/?origin=&date_from=&date_to=`: stage moves with mean time in the source stage, per-stage
  current / entered / left / mean time, companies per origin — aggregated by the database in four queries.

## 0.4.0 — 2026-10-12

- **Toolbox switched off (`AI_TOOLBOX_ENABLED = False`).** Nothing fails and nothing alerts. An intel analysis is
  skipped: Activity `analysis skipped: AI toolbox disabled`, claim `retry` with no failure counted, no rules;
  `retry_failed_analyses` runs it once the toolbox is on. `request_draft` records `skipped: AI toolbox disabled`
  and re-raises `ToolboxDisabledError` for an AI template: `POST companies/<id>/communicate/` answers 503
  `AI_TOOLBOX_DISABLED`, a rule run is `skipped` (no cooldown), a rotation waits in `retry` without a counted
  failure. `ai_pick` keeps its fallback to the first eligible contact. Needs the entirius-django-communicator and
  entirius-django-utils releases that carry the switch.
- Access: the module declares its own access areas on its AppConfig and its admin views (copied from the
  entirius-django-access defaults; behaviour unchanged).
- OpenAPI: tags in Title Case (`Leads Activities`, `Companies`, `Contacts`, `Imports`, `Lead Types`, `Analysis
  Profiles`, `Recipient Profiles`, `Rules`, `Stages`), also in `docs/openapi.yaml`. Metadata only; a client that
  groups by tag name sees the new names.
- Requires `entirius-django-utils` 2.3.0 and `entirius-django-communicator` 0.4.0 or later (the AI toolbox switch).

## 0.3.0 — 2026-09-30

- **Release order:** outreach now calls `communicate(audience=…)`, which needs the next entirius-django-communicator
  release (template `audience`, migration `0010`). Raise the `entirius-django-communicator` pin to that version when
  releasing this module — against communicator 0.2.0 every communicate rule fails with `TypeError`.
- **Lead types are configuration per channel** (`LeadType`: `code`, `label`, `order`, `is_active`), replacing the
  hard-coded `CompanyType` enum. `Company.company_type` is renamed `lead_type` (migration `0007`, which also creates a
  row for every code a company of a channel already carries); the API field, the company filter (`lead_type`) and the
  outreach context field follow. `UNKNOWN` stays built in.
- Admin API `lead-types/` (list, create, detail, patch, delete; `LEAD_TYPE_EXISTS`, `LEAD_TYPE_IN_USE`). The filter
  and company create/patch accept only an active code of the channel or `UNKNOWN` (400 otherwise).
- CSV import reads `lead_type`, still accepts a `company_type` column; a code outside the channel's active lead types
  imports as `UNKNOWN` (it used to be stored blank).
- Intel: the analysis prompt gets the channel's lead types as `{lead_types}`; the guess (`lead_type_guess`, or
  `company_type_guess` for one release) is kept only when it is an active code.
- Outreach passes the company's lead type to communicator `communicate(audience=…)`, so one rule template key picks
  the lead-type variant of the template (needs django-communicator with template audiences).
- Admin API `DELETE contacts/<id>/`: a never-used contact is deleted (204, `contact removed` note on the timeline);
  a used one — thread, outreach Activity, consent, opt-out — is anonymised instead (200 with the row).
- One primary contact per company: making a contact primary (create or PATCH) unsets the others.
- `PATCH contacts/<id>/` takes `email` while the contact has none (409 `CONTACT_EXISTS` on a duplicate); a set email
  stays immutable (400).

## 0.2.0 — 2026-09-16

- **Intel recovery after a toolbox outage.** An analysis that failed transiently (`ToolboxConnectionError`, timeout,
  HTTP 5xx) leaves its claim in `retry`; new beat task `django_leads.retry_failed_analyses` (host schedule every
  10 min) runs it again once `status()` reports the toolbox reachable and the audit is still valid, at most
  `LEADS_INTEL_RETRY_LIMIT` (3) times, then evaluates the `intel_ready` rules. Budget, model and schema failures
  stay failed. The alert fires on the first and the final failure only. Development endpoint
  `test/retry-analyses/`.
- An analysis retry superseded by a newer audit of the domain or a later successful analysis ends `failed/superseded`
  without spending a retry; `retry_failed_analyses` runs once at a time (`QueueOnce`). Draft retries in communicator
  honour the outreach gate (`do_not_contact`, opt-out, anonymised, no legal basis) through `draft_retry_requested`.
- `CompanyDetailResponse` carries `customer_name` — the linked customer's display name, so a card shows a name
  instead of the raw uid. Empty without `django_accounts`; the response stays backward compatible.
- Only a newer audit that completed (or partially completed) supersedes an analysis retry — a failed re-audit leaves
  the retry valid. A draft retry refused by the outreach gate records one `blocked: <reason>` Activity per draft.

## 0.1.0 — 2026-09-15

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
