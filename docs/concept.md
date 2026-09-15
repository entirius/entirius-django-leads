---
title: Concept
description: The B2B pipeline model, how a company moves through it, and how leads drives communicator, siteintel, agreements and notifications.
---

django-leads keeps prospect companies and the people at them in a per-channel pipeline, and decides when
outreach may happen. It never writes an email itself: drafts are requested from communicator, legal footers come
from agreements, website facts from siteintel, human attention from notifications.

## Domain model

| Model | Holds | Key rule |
|---|---|---|
| `Channel` | `idx`, languages, `retention_days` | own scoping model (created in Django admin, never from signals); its `idx` is the same channel in communicator, agreements and siteintel |
| `Stage` | `key`, `label`, `order`, `kind` (`open`, `won`, `lost`, `unresponsive`), `is_terminal`, `on_reply` | new companies enter the lowest `order`; unique `(channel, key)` |
| `Company` | name, `domain`, website, type, industry, `platform`, `hooks`, stage, `source`, `do_not_contact`, `customer_uid`, `rotation_count`, `last_activity_at` | unique `(channel, domain)`; `domain` is always the registrable domain |
| `Contact` | email, names, job title, phone, language, `is_primary`, `legal_basis`, `opt_out_at`, `anonymised_at` | unique `(company, email)` for non-empty emails; `legal_basis` null = none recorded |
| `Activity` | `kind`, `message`, `data`, `actor` per company (and contact) | written only by `activity_service`, which also moves `Company.last_activity_at` |
| `ImportBatch` | counters, `row_count`, `last_row_done`, `size_bytes`, capped `report` | never the CSV itself |
| `StageRule` | trigger, stage, action, template, contact strategy, conditions, cooldown, order | evaluated by `rule_service` |
| `RuleRun` | one evaluation: `outcome`, `state`, `event_key`, `detail` | unique `(rule, company, event_key)` for non-empty keys |
| `Claim` | `key`, `state` (`claimed`, `done`, `retry`, `failed`), `failures` | idempotency of paid calls outside rules |
| `AnalysisProfile` / `RecipientPickProfile` | prompt, JSON schema, toolbox model per channel and key | never logged, never in API lists |
| `ErasedAddress` | token of an erased or anonymised address | never the address |

`LeadSource` of companies and contacts: `csv`, `form`, `manual`, `connector`.

## Deduplication

- Company: `utils/domains.registrable_domain` — tldextract with its bundled public suffix list (never the network),
  plus the reserved `.test` TLD. `www.shop.pl/pl` and `shop.pl` are one company; `myshop.pl` is another (never
  substring matching). A public suffix such as `shop.pl` alone is not registrable.
- Contact: `utils/emails.normalize_email` (trimmed, lower-cased); a contact without email matches by first + last
  name within the company.
- A match fills empty fields only. `upsert_company` / `upsert_contact` are race-safe (insert in a savepoint, re-read
  on `IntegrityError`) and write no Activity — the caller records its own.

## How companies enter

| Path | Entry | Result |
|---|---|---|
| CSV import | `leads_import_csv`, `POST imports/`, `test/import-now/` | companies + contacts in chunks, Activity `import` per row, batch report (`operations.md` § Import) |
| Contact form | contact_forms `lead_status_changed` on creation, after commit | company from the first website key, else the email host (free-mail hosts skipped); Activity `form` "form submission" |
| Manual | `POST companies/`, `POST contacts/` | an existing domain or email is a 409, never a silent match |
| Connector | `connectors/` — `Connector` protocol, `leads_connectors` entry points | `csv` feeds every CSV import; `twenty` raises `NotImplementedError` in v1 |

### Legal basis

Never filled like other fields. Import and form call `contact_service.propose_legal_basis`: an empty basis is set
with a `legal_basis` Activity (`from`, `to`, `source`, `consent_ref`); a different recorded basis stays and the
attempt is logged as `legal_basis_conflict`. An operator changes it explicitly through the API (`set_legal_basis`).
`consent` always carries a reference: `form:<lead id>`, `import:<batch>:<row>` or `admin:<user>:<consent_ref>`.

A form grants consent only for `True`, `1`, or `true`/`1`/`yes`/`on`/`y`/`tak` in a `LEADS_FORM_CONSENT_KEYS` key
(HTML forms send `"false"` and `"off"` as strings). A contact left without a basis gets Activity "no legal basis".

## Lifecycle

```
import / form / manual ──▶ first stage
        │  transition_stage (API, reply, rotation) ──▶ stage_entered ──▶ rules (stage_entered)
        │  request_audit (rule or API) ──▶ siteintel audit ──▶ report_ready ──▶ analysis ──▶ rules (intel_ready)
        │  rule / manual communicate ──▶ outreach gate ──▶ communicator draft (review required)
        │  message_sent ──▶ Activity sent        reply_received ──▶ on_reply stage + high notification
        │  sequence_finished without reply ──▶ rotation to the next contact … ──▶ unresponsive stage
        ▼  idle past retention ──▶ contacts anonymised (company, stage, timeline kept)
```

`stage_service.transition_stage` is the only stage change: it writes Activity `stage` and sends `stage_entered`
after commit. A company created by an import enters its first stage without the signal. A stage holding companies
cannot be deleted.

## Rules

`rule_service.evaluate_rules(company, trigger, stage=None, event="")` runs the active rules of the trigger (and
stage) by `order`, each in three phases:

1. **Claim, under the company row lock** on the re-read row. A run of (rule, company, `event`) already exists →
   returned, nothing runs again (`event` = `stage:<id>:<entered_at>` from `stage_entered`, `audit:<id>` from intel;
   empty = no dedupe). Then in order: `do_not_contact` → `blocked`; a `fired` or in-flight run inside
   `cooldown_hours` → `cooldown`; action `request_audit` → siteintel audit, `fired`; `require_hooks` without hooks →
   `skipped`; `require_email` without a candidate contact → `skipped`; otherwise the run is written `claimed`.
2. **Outreach, outside the lock.** Recipient pick among gate-eligible contacts (none → `blocked:
   no_eligible_contact`, no toolbox call), then `outreach_service.request_draft`.
3. **Complete** the run (`fired` with `message <id> <status>`, `blocked`, or `skipped` "no draft").

Skipped, blocked and cooldown runs never start a cooldown. A `claimed` run older than `LEADS_CLAIM_STALE_MINUTES`
found by a redelivery is failed `outcome_unknown` and never retried. Any error becomes Activity
`rule error: <class>` and a skipped run; evaluation never raises.

Recipient pick: `primary` takes the first eligible contact (primary first). `ai_pick` with more than one eligible
contact sends `{"candidates": [...]}` to the toolbox (`RecipientPickProfile` `leads.pick_recipient`); an answer
outside the candidates, a toolbox error or a missing profile falls back to the first eligible contact with Activity
`rule` "recipient pick rejected: <reason>".

## Outreach gate

`recipient_service.block_reason(contact)` in this order: `do_not_contact`, `no_email`, `opted_out`, `anonymised`,
`no_legal_basis`. It is evaluated only inside `outreach_service.request_draft`, in one short transaction that locks
the company, then the contact, and calls communicator `communicate()` on those rows — an opt-out or `do_not_contact`
either commits first (blocked) or waits for the draft to commit. Every path (rules, rotation, manual communicate)
goes through it exactly once; a refusal is one Activity `blocked: <reason>`, never a draft. The gate is the only
legal-basis decision.

## Integration

| Module | leads calls | leads receives |
|---|---|---|
| communicator | `communicate(channel_idx, template_key, recipient, context, subject_ref="leads.Company:<id>", requires_review=True)` — the only caller from leads; leads never writes a `Message` | `reply_received` → Activity `reply`, `on_reply` stage, high notification; `company_skipped` (reviewer) → `do_not_contact`, Activity `blocked`; `message_sent` → Activity `sent`; `sequence_finished` → rotation task |
| agreements | `resolve_clause_set(channel_idx, legal_basis, language)` + `render_legal_footer` (contact language, else channel default); `LegalBasis` enum | — (a missing clause set → Activity `skipped: no clause set`, no draft) |
| siteintel | `request_audit(domain, channel_idx, requested_by)` | `report_ready(audit, succeeded_sources)` → analysis task |
| toolbox (`django_utils.toolbox`) | one completion per analysis (`AnalysisProfile` `leads.analysis`) and per `ai_pick` | — |
| notifications (soft) | `notify(channel_idx, recipient_role=LEADS_NOTIFY_ROLE, severity, subject_ref, title, body)` | — |
| contact_forms (soft) | reads `Lead`, never writes it | `lead_status_changed` on creation |
| accounts (soft) | verified `EmailAddress` → `Customer.uid` on `create-customer/` | — |

Emitted: `stage_entered(company, stage)` (own receiver → `evaluate_rules` task) and
`contact_anonymised(email_hash, anonymised_email, subject_ref)` — communicator suppresses the token on every
channel and anonymises its threads. All receivers use `dispatch_uid="django_leads.<name>"`, run after commit, never
raise into the sender, and handle only `subject_ref` `leads.Company:<int>`.

### Intel analysis

`report_ready` → task `analyse_intel` → claim `intel:<audit>:<task id>` (a redelivered message keeps its task id and
finds the claim) → one toolbox completion, never retried → `hooks` (≤ `LEADS_ANALYSIS_MAX_HOOKS`), `platform`, and
`company_type` when it was `UNKNOWN` → Activity `intel` → `intel_ready` rules. No succeeded source → no toolbox
call, hooks cleared, Activity `intel_empty`, then the rules (never on stale hooks). A failure → Activity
`analysis failed: <code>` + medium notification, no rules.

Prompts (`utils/prompts`): the system message is the static `DATA_INSTRUCTIONS`; the profile template is rendered
into the user message in one pass (a value is never expanded again) with every company and site value wrapped in
`<company_data>…</company_data>`, the delimiter stripped from the value.

### Rotation

`sequence_finished` → task `rotate_thread`; the daily `rotate_unresponsive` catches threads the receiver missed.
Claim `rotation:<thread>` under the company row lock: another rotation of the company in flight → nothing now;
`do_not_contact` → Activity `blocked`, claim `done`; `LEADS_ROTATION_MAX` reached or no next gate-eligible contact
not yet written to → Activity "rotation exhausted", transition to the `unresponsive` stage, claim `done`. Otherwise
the draft (template of the last draft) is requested outside the lock; only a draft raises `rotation_count` and
writes Activity `rotation`. No draft → `rotation_failed`, claim `retry` after `LEADS_ROTATION_RETRY_HOURS`; after
`LEADS_ROTATION_MAX_FAILURES` → `rotation_gave_up`, claim `failed`.

## GDPR and retention

Rows are never deleted. Retention and erasure pseudonymise a contact: email → deterministic token
`anon-<sha256[:16]>@LEADS_ANONYMISED_DOMAIN`, names, job title and phone cleared, `anonymised_at` set, token kept in
`ErasedAddress`, Activity `anonymised` with `email_hash`, `contact_anonymised` on commit — once, under a row lock.
Company, stage, hooks, counters and timeline stay. An erased address is never re-imported (`erased_address`) nor
re-created from a form.

GDPR hooks: every installed app's top-level `<app>.gdpr` module with `gdpr_export(email) -> dict` and
`gdpr_erase(email) -> dict[str, int]` (`gdpr/protocol.GdprHooks`) is discovered at call time. Erasure runs in one
transaction after a `note` "gdpr erase requested" on every affected company; leads' own hook anonymises the contacts
(actor `gdpr`) and scrubs their Activities to `[erased]`. Matching covers the address and its token.

`utils/emails.email_hash` / `anonymised_address` are canonical; communicator and agreements keep local copies (no
dependency on leads) pinned by parity tests. Operating it: `operations.md`.
