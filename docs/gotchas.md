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
  submissions are only read.
- **`utils/emails` token functions are copied, not imported,** into communicator and agreements. A change here
  without the copies breaks suppression; `tests/test_retention.py` pins all three.

## Import

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

## Rules, gate and paid calls

- **The outreach gate runs only inside `outreach_service.request_draft`,** on locked company then contact rows. A new
  outreach path calls `request_draft`; it never checks `block_reason` itself and never calls `communicate()`.
- **Lock order is company, then contact** everywhere. The reverse deadlocks against a concurrent draft.
- **No toolbox call inside a transaction or under a row lock** (`test_no_toolbox_call_inside_atomic`). Claims are
  committed before the paid call and completed after it.
- **A claim is never retried after `LEADS_CLAIM_STALE_MINUTES`** (`outcome_unknown`): the call may have been paid.
  Rotation's `retry` state is the only automatic second attempt, and only after no draft was produced.
- **Skipped, blocked and cooldown runs never start a cooldown.** Counting them would silence a company after one
  missing email.
- **`event` dedupes redelivery; an empty `event` does not.** `test/evaluate/` passes none, so calling it twice runs
  the rules twice (cooldown still applies to fired runs).
- **`StageRule.require_legal_basis` has no effect** and is not exposed by the API — the gate is the only legal-basis
  decision.
- **A company created by an import does not send `stage_entered`.** Its first-stage rules run only after a transition
  or through `test/evaluate/`.
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

- **Rows are never deleted** — anonymisation clears personal fields and keeps company, stage and timeline.
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
