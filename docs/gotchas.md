---
title: Gotchas
description: The one list of rules that bite — read before touching imports, rules, the outreach gate, receivers or GDPR.
---

Install-time traps (shared temp dir, `leads_default` queue, beat schedule, celery-once) live in `install.md`. Each
item below: the rule, then where it is enforced.

## Contracts

- **Never rename the package, the app label or the table prefix `django_leads`**, and never edit a released
  migration — both are schema contracts.
- **Settings are read once, at import** (`django_leads/settings.py` is `getattr` at module level, and tasks take
  their queue at decoration). `override_settings` does not reach them: tests monkeypatch `django_leads.settings`
  attributes. That patch reaches only code that reads `leads_settings.X` through the module; `alert_service`
  (`LEADS_NOTIFY_ROLE`) and `intel_service` (`LEADS_ANALYSIS_MAX_HOOKS`) import the value and ignore it.
- **`Activity` is written only through `activity_service`** — it moves `Company.last_activity_at`, which retention
  reads. A bulk `Activity.objects.create` elsewhere makes a company look idle.
- **`transition_stage` is the only stage change.** Setting `company.stage` directly skips the timeline and
  `stage_entered`, so no rule runs. Django admin keeps `stage` read-only and cannot add companies for that reason.
- **Leads never writes a communicator `Message` or a contact_forms `Lead`** — drafts go through `communicate()`,
  hand-written mail through `compose()`, submissions are only read.
- **`utils/emails` token functions are copied, not imported,** into communicator and agreements. A change here
  without the copies breaks suppression; `tests/test_retention.py` pins all three.

## Import

- **A lead type is a code, not a foreign key.** `Company.lead_type` keeps the string even when the `LeadType` row
  turns inactive; only the filter, create/patch, the import and the intel guess check `lead_type_service.active_codes`.
  The code is fixed after create (companies and communicator template audiences hold it) and a type in use cannot be
  deleted — deactivate it instead.

- **No CSV content in the broker, the database or the logs.** The task message is the batch id; the report keeps row
  numbers and reason codes only. A new skip reason is a code, never the offending value.
- **Only `OperationalError` may leave `run_file`** — it is the task retry. Anything else must end the batch
  `failed` with a code; a batch left `running` waits for the stale sweep.
- **A chunk's counters and `last_row_done` commit with its rows.** Moving the progress save outside the chunk
  transaction makes a retry replay or skip rows.
- **The per-chunk query budget is ≤ 3 selects plus bulk writes** (L-05). Per-row lookups belong in `Lookups`
  (fetched once per batch) or in the chunk prefetch.
- **`upsert_company` / `upsert_contact` write no Activity.** Callers (import, form) record their own — an extra
  Activity there doubles every timeline.
- **`legal_basis` is never filled by `fill_empty`** — only `propose_legal_basis` (import, form) or `set_legal_basis`
  (operator). `consent` without a `consent_ref` raises `ConsentRefRequired`.

## Attributes, conditions and scoring

- **`attributes` is written only through `attribute_service`** (`initial`, `apply`, `fill_missing`, the import's
  `clean_value`). It is never in an `EDITABLE_FIELDS` whitelist and never set from a request dict.
- **Decisions read facts, not columns.** Rule conditions, scoring and the template context go through
  `fact_service.facts`; a new built-in fact is added there (`BUILTIN_KINDS`), never read ad hoc.
- **A condition naming an unknown field is refused at save** (`condition_service.check_all`), never at run time. A
  definition deactivated later reads as an empty fact — the condition then fails (only `ne` / `empty` hold).
- **`attributes` is merged on the locked row.** A patch re-reads the JSON under `select_for_update`
  (`attribute_service.apply_locked`), and the import chunk locks the companies and contacts it prefetches (company
  first) because `bulk_update` writes the JSON back whole.
- **The list filter is a containment lookup** (`attributes__contains`) so the GIN index serves it; an
  `attributes__<key>` lookup would scan.

## Rules, gate and paid calls

- **The outreach gate runs only inside `outreach_service.request_draft` and `compose_message`,** on locked company
  then contact rows. A new outreach path calls one of them; it never checks `block_reason` itself and never calls
  `communicate()` or `compose()`.
- **Only `services/task_bridge.py` (and `signals/`) import `django_todo`.** Every bridge call is a no-op without it;
  a new caller goes through the bridge, so the module stays optional.
- **Lock order is company, then contact** everywhere. The reverse deadlocks against a concurrent draft.
- **No toolbox call inside a transaction or under a row lock** (`test_no_toolbox_call_inside_atomic`). Claims are
  committed before the paid call and completed after it.
- **A claim is never retried after `LEADS_CLAIM_STALE_MINUTES`** (`outcome_unknown`): the call may have been paid.
- **Only a transient toolbox failure or a switched-off toolbox leaves an intel claim in `retry`**
  (`intel_service.is_transient`, `AnalysisSkipped` — the latter counts no failure). The retry
  takes the claim `retry → claimed` by compare-and-set before its one completion; a `claimed` retry that never
  finishes stays claimed — never re-run.
  Rotation's `retry` state is the only automatic second attempt, and only after no draft was produced.
- **Skipped, blocked and cooldown runs never start a cooldown.** Counting them would silence a company after one
  missing email.
- **`event` dedupes redelivery; an empty `event` does not.** `test/evaluate/` passes none, so calling it twice runs
  the rules twice (cooldown still applies to fired runs).
- **`StageRule.require_legal_basis` has no effect** and is not exposed by the API — the gate is the only legal-basis
  decision.
- **An import fires first-stage rules only with `run_rules`.** Without it a created company sends no `stage_entered`
  (rules run after a transition or through `test/evaluate/`). With it every created company costs one
  `evaluate_rules` task, sent after its chunk commits: a rolled-back chunk or bulk write fires nothing, a matched row
  never fires, a redelivered event is deduped by `event`.
- **`opt_out_at` and `do_not_contact` have one writer each** (`contact_service.opt_out`,
  `company_service.set_do_not_contact`); a direct write skips the communicator suppression. `do_not_contact` is not in
  `update_company`'s whitelist.
- **A stage moves on an event only through the map** (`stage_event_service.apply` → `transition_stage`); it never
  leaves `won` / `lost`. There is no `Stage.on_reply` any more.
- **Profile prompts and rendered prompts never reach logs, Activity data or API lists.** Company and site values go
  into prompts only wrapped by `utils/prompts.as_data`.

## Receivers

- **Every receiver defers with `signals/_deferred.after_commit` and never raises into the sender,** with
  `dispatch_uid="django_leads.<name>"`. A receiver that raises would roll back a communicator send or a form
  submission.
- **Only `subject_ref` `leads.Company:<int>` belongs to leads** (`utils/refs.company_from_ref`); other modules' threads
  share the signals.
- **GDPR hooks are discovered at call time** (`gdpr/registry.discover`), never in `ready()`. A `gdpr` module missing
  either hook raises `ImproperlyConfigured` on the first export or erase, not at boot.

## GDPR and retention

- **Rows are never deleted** — anonymisation clears personal fields and keeps company, stage and timeline. The one
  delete is an operator removing a contact nothing used (`contact_service.remove_contact`); a used one is anonymised.
- **A blank email must never reach `contacts_of_email`** — it would match every contact without an address; it raises
  `ValueError`, and the command and API validate first.
- **`anonymise_contact` re-reads the contact `select_for_update(of=self)` and skips one already anonymised,** so erase
  and retention racing write the Activity and send `contact_anonymised` once.
- **`LEADS_ANONYMISED_DOMAIN` must be one value across leads, communicator and agreements** — the token is compared
  as a string.
- **A company without any Activity is never picked by retention** (`last_activity_at` null).

## API

- **Named 409 bodies differ from handler 409s.** `NotEligible` and `NotImplemented` are built by the view; every
  other 409 passes the v2 handler, which drops the detail (`api.md` § Errors).
- **`create-customer/` is routed only when `django_accounts` is installed,** and its module is imported only then
  (L-15). Never import `customer_link_views` unconditionally.
- **`test/anonymise-now/` runs retention for every channel,** not only the one in the path.
