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
| `Channel` | `idx`, languages, `retention_days`, `resume_days`, `default_currency` (FK `django_regional.Currency`, null) | own scoping model (created in Django admin, never from signals); its `idx` is the same channel in communicator, agreements and siteintel |
| `Stage` | `key`, `label`, `order`, `kind` (`open`, `won`, `lost`, `unresponsive`), `is_terminal` | new companies enter the lowest `order`; unique `(channel, key)` |
| `StageEventRule` | `event`, `target_stage` (null = keep the stage), `forward_only` | the event → stage map; unique `(channel, event)` |
| `Company` | name, `domain`, website, `lead_type` (a code), industry, `platform`, `hooks`, stage, `source`, `do_not_contact`, `customer_uid`, `rotation_count`, `last_activity_at`, `origin` (free text), `attributes`, `score_points`, `score_segment`, `scored_at`, `owner` (FK user, null) | unique `(channel, domain)`; `domain` is always the registrable domain; GIN index on `attributes`; index `(channel, owner)` |
| `LeadType` | `code`, `label`, `order`, `is_active` | configuration per channel, unique `(channel, code)`; `Company.lead_type` holds the code (no FK); `UNKNOWN` is built in, never a row |
| `Contact` | email, names, job title, phone, language, `is_primary`, `legal_basis`, `opt_out_at`, `anonymised_at`, `wrong_person_at`, `attributes` | unique `(company, email)` for non-empty emails; `legal_basis` null = none recorded |
| `AttributeDefinition` | `entity` (`company`, `contact`, `deal`), `key`, `label`, `kind`, `options`, `required`, `order`, `show_in_list`, `is_active` | unique `(channel, entity, key)`; the kind is fixed once a value is stored |
| `ScoringProfile` | `key`, `thresholds {S, M, L}`, `rules`, `disqualifiers`, `is_active` | unique `(channel, key)`; the first active profile by key scores the channel |
| `DealStage` | `key`, `label`, `order`, `kind` (`open`, `won`, `lost`) | the sales pipeline; unique `(channel, key)`; a stage holding deals cannot be deleted nor change kind |
| `Deal` | `company` (`CASCADE`), `contact` (null, `SET_NULL`), `title`, `owner` (FK user, null), `stage` (`PROTECT`), `stage_entered_at`, `value_one_off` / `value_recurring` (`Decimal(14, 2)`, null), `recurring_period` (`month`, `year`), `currency` (FK `Currency`, null, `PROTECT`), `expected_close_date`, `closed_at`, `lost_reason`, `attributes`, `created_by` | written only by `deal_service` (owner: `owner_service`); indexes `(channel, stage)`, `(channel, owner)`, `(company)` |
| `Activity` | `kind`, `message`, `data`, `actor` per company (and contact, and deal) | written only by `activity_service`, which also moves `Company.last_activity_at` |
| `ImportBatch` | counters, `row_count`, `last_row_done`, `size_bytes`, capped `report`, `run_rules`, `owner` (FK user, null) | never the CSV itself |
| `StageRule` | trigger, stage, action, template, contact strategy, conditions, cooldown, order | evaluated by `rule_service` |
| `RuleRun` | one evaluation: `outcome`, `state`, `event_key`, `detail` | unique `(rule, company, event_key)` for non-empty keys |
| `Claim` | `key`, `state` (`claimed`, `done`, `retry`, `failed`), `failures` | idempotency of paid calls outside rules |
| `AnalysisProfile` / `RecipientPickProfile` | prompt, JSON schema, toolbox model per channel and key | never logged, never in API lists |
| `ErasedAddress` | token of an erased or anonymised address | never the address |

`LeadSource` of companies and contacts: `csv`, `form`, `manual`, `connector`.

## Owner

`Company.owner` is the salesperson of the company: the first (and so far only) relation from leads to the user model
(`AUTH_USER_MODEL`, `SET_NULL`, no reverse accessor). Only `owner_service` writes it — it is never in the generic
`EDITABLE_FIELDS` whitelist — and every change is an Activity `note` "owner: <username | none>".

- **Who can own**: active staff users, without the service users of access applications (`access_application`, when
  `django_access` is installed). A deactivated owner keeps their companies; the picker no longer offers them.
- **How a company gets one**: manual create (default: the requesting user, when they can own), company patch, bulk assign (≤ 200), an
  import with `owner_id` (created companies, and matched ones without an owner). The contact form and an import
  without `owner_id` leave the company unowned.
- **What follows it**: alerts (`alert_service`) go to the owner (`recipient_user_id`) while their account is active,
  else to `LEADS_NOTIFY_ROLE`;
  drafts (`outreach_service`, so rules and rotation too) ask communicator for the owner's mailbox
  (`mailbox_user_id`), which falls back to the channel default.
- **Visibility**: everyone with the area `leads.companies` sees every company; the owner is a filter (`owner=me`),
  not a permission. The owner is staff, not a data subject: GDPR export and erasure do not touch it.

## Attributes and facts

A channel defines its own fields (`AttributeDefinition`); their values sit in the entity's `attributes` JSON, written
only through `attribute_service` (create / patch merge, the CSV import, the import's fill-empty rule) — never set from
a request dict. Built-in fields (`lead_type`, `industry`, `platform`, `hooks`, …) stay columns: the site audit and the
lead types write them.

Everything that decides reads a company through one accessor, `fact_service.facts(company, contact=None)`: the
built-ins `lead_type`, `industry`, `platform`, `domain`, `origin`, `stage` (key), `hooks_count`, `emails_count`
(contacts with an email, not anonymised), `rotation_count`, `score_points`, `score_segment`, plus `attr.<key>` for
every active company attribute (`None` without a value) and, given a contact, `contact.attr.<key>`.
`fact_service.fields(channel)` describes the same names (kind, label, options) for validation and the CMS
(`GET fields/`). Rule conditions, scoring and the template context use it; the list filter uses the database
(`attributes @> {key: value}`, served by the GIN index).

## Scoring

`scoring_service.score(company)` sums the `points` of the profile's rules whose condition holds on the facts;
points ≥ L → `S1`, ≥ M → `S2`, ≥ S → `S3`, else `S4`; a matching disqualifier forces `S4`. No toolbox, no network.
`score_and_record` stores `score_points`, `score_segment`, `scored_at` on the company and writes Activity `score`
with the reasons as text (`Retailer: +30`, `disqualified: <label>`) and as data (`{field, kind, points, label}`). It
runs from `POST companies/<id>/score/` and from the rule action `score`.

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
        │  message_sent ──▶ Activity sent (+ first_sent)   reply_received ──▶ reply:<class> + plan's notification
        │  sequence_finished ──▶ (sequence_finished) ──▶ rotation to the next contact … ──▶ unresponsive stage
        ▼  idle past retention ──▶ contacts anonymised (company, stage, timeline kept)
```

`stage_service.transition_stage` is the only stage change: it writes Activity `stage` and sends `stage_entered`
after commit. A company created by an import enters its first stage without the signal, unless the import runs with
`run_rules` (one `stage_entered` per created company after its chunk commits). A stage holding companies cannot be
deleted.

### Event → stage map

`StageEventRule` rows decide where an event moves a company (`stage_event_service.apply`, through
`transition_stage`). Events: `first_sent` (the company's first `message_sent`), `reply`, `reply:<class>` (`interested`,
`not_now`, `not_interested`, `out_of_office`, `wrong_person`, `unsubscribe`, `unclear` — communicator's reply class),
`sequence_finished`. A classified reply looks up `reply:<class>`, else `reply`; a row with a null target keeps the
stage (and stops the fallback); no row at all moves nothing. A move never leaves a stage of kind `won` or `lost`;
with `forward_only` (the default) it happens only to a stage of a higher `order`. `sequence_finished` moves first and
then starts the rotation as before; the rotation's `unresponsive` parking is not part of the map. Migration 0009
turned every `Stage.on_reply` into a `reply` row (`forward_only=False`, the old behaviour) plus a
`reply:out_of_office` row that keeps the stage.

## Rules

`rule_service.evaluate_rules(company, trigger, stage=None, event="")` runs the active rules of the trigger (and
stage) by `order`, each in three phases:

1. **Claim, under the company row lock** on the re-read row. A run of (rule, company, `event`) already exists →
   returned, nothing runs again (`event` = `stage:<id>:<entered_at>` from `stage_entered`, `audit:<id>` from intel;
   empty = no dedupe). Then in order: `do_not_contact` → `blocked`; a `fired` or in-flight run inside
   `cooldown_hours` → `cooldown`; a `conditions` entry that does not hold → `skipped` with Activity `skipped: condition
<field>` (no cooldown starts); action `score` → `scoring_service.score_and_record`, `fired` (`skipped: no scoring
profile` without one); action `request_audit` → siteintel audit, `fired`; `require_hooks` without hooks →
   `skipped`; `require_email` without a candidate contact → `skipped`; otherwise the run is written `claimed`.
2. **Outreach, outside the lock.** Recipient pick among gate-eligible contacts (none → `blocked:
   no_eligible_contact`, no toolbox call), then `outreach_service.request_draft`.
3. **Complete** the run (`fired` with `message <id> <status>`, `blocked`, or `skipped` "no draft"). An AI template
   while the toolbox is switched off → `skipped` with `skipped: AI toolbox disabled`, which starts no cooldown.

Skipped, blocked and cooldown runs never start a cooldown. A `claimed` run older than `LEADS_CLAIM_STALE_MINUTES`
found by a redelivery is failed `outcome_unknown` and never retried. Any error becomes Activity
`rule error: <class>` and a skipped run; evaluation never raises.

Recipient pick: `primary` takes the first eligible contact (primary first). `ai_pick` with more than one eligible
contact sends `{"candidates": [...]}` to the toolbox (`RecipientPickProfile` `leads.pick_recipient`); an answer
outside the candidates, a toolbox error or a missing profile falls back to the first eligible contact with Activity
`rule` "recipient pick rejected: <reason>".

## Outreach gate

`recipient_service.block_reasons(contact)` lists every refusal in this order: `do_not_contact`, `no_email`,
`opted_out`, `anonymised`, `wrong_person`, `no_legal_basis`, then communicator's `can_mail` code (`suppressed`,
`address_invalid`) for a contact with an email; `block_reason` is the first. It is evaluated only inside `outreach_service.request_draft`, in one short transaction that locks
the company, then the contact, and calls communicator `communicate()` on those rows — an opt-out or `do_not_contact`
either commits first (blocked) or waits for the draft to commit. Every path (rules, rotation, manual communicate)
goes through it exactly once; a refusal is one Activity `blocked: <reason>`, never a draft. The gate is the only
legal-basis decision. `GET companies/<id>/contacts/<contact_id>/mailable/` shows the same reasons with the legal
basis, communicator's `explain` and the retention clock — a read, never the gate.

### Do not contact

One writer each: `contact_service.opt_out(contact, reason=, actor=)` sets `opt_out_at` once (Activity `optout`) and
suppresses the address in communicator (source `manual`); `company_service.set_do_not_contact(company, value,
actor=)` sets the flag (Activity `note`), suppresses the company's domain when turned on and removes that domain row
(only the one leads wrote) when turned off. A confirmed opt-out from communicator opts out every contact with the
address in its channel, or in every channel when communicator's suppression scope is `install`. A `wrong_person`
reply sets `Contact.wrong_person_at`; the gate, the recipient pick and the rotation skip the contact.

The draft passes the company's `lead_type` as communicator's `audience`: one `template_key` per rule, the lead type
picks the template variant — communicator's cascade (language, lead type) → (language, any) → (channel default
language, lead type) → (default language, any). A type without its own variant gets the blank-audience template; no
template at all is communicator's `no_template` failure, as before. Follow-ups of a sequence resolve without an
audience (communicator does not keep one per thread).

## Two levels

- **Company stages** (`Stage`, `stage_service`): prospecting — new, contacted, replied, qualified, unresponsive … The
  outbound automation (rules, sequences, rotation, the event → stage map) runs on them.
- **Deals** (`DealStage`, `deal_service`): selling — one sales pipeline per channel, stages of kind `open`, `won` or
  `lost`. A person opens a deal on a company; nothing opens one automatically.

The two never move each other: a won deal leaves the company's stage where it is (moving a customer to the company
stage "won" stays a person's move), and no rule fires on a deal. Different tables, different services.

## Deals

- **Opening**: `deal_service.create` puts the deal in the channel's first `open` stage by `order` (none → 409
  `no_deal_stage`). The owner defaults to the company's owner while active, then to the creator when they can own;
  the currency to `Channel.default_currency`. Activity `deal` "deal opened: <title>" on the company timeline.
- **Moving**: `deal_service.transition` — any stage to any stage of the channel. Entering `won` or `lost` sets
  `closed_at`; `lost` requires `lost_reason`; leaving a closed stage clears both. Activity `deal`
  "deal <from> -> <to>" with `data = {deal_id, from, to}`; the signal `deal_stage_changed(deal, previous, stage)` is
  sent on commit. The API names the target by key or by kind (= the first stage of that kind; a channel without one
  refuses with 409 `no_deal_stage`).
- **Editing**: `deal_service.update` whitelists `title`, `contact_id` (a contact of the same company), the two values,
  `recurring_period`, `currency`, `expected_close_date` and `attributes` (entity `deal`, through
  `attribute_service`). `owner` changes through `owner_service.set_deal_owner`, `stage` only through `transition`;
  `company` and `closed_at` are never editable.
- **Money**: `Decimal` end to end, decimal strings in the API (`"12000.00"`, never a JSON number), never negative, and
  a value without a currency is refused. The board totals per currency (recurring per month: a yearly value counts
  as a twelfth) and never adds currencies.
- **Deleting**: open deals only (409 `deal_closed`); the timeline keeps "deal deleted: <title>" with the old id and
  the deal's open tasks are cancelled ("deal deleted").
- **Concurrency**: an update saves only the patched columns, so it never writes back a stage or owner changed by a
  concurrent request.
- **On the company**: the company card counts its deals per kind; `companies/<id>/deals/` lists them.
- **Tasks**: the CMS creates tasks about a deal with `subject_ref = leads.Deal:<id>` and `subject_label` =
  "<company> — <title>" (both in every deal response); the timeline receivers write them on the deal's company with
  the deal set, and `open_tasks` counts them.

## Tasks

The pipeline tells the owner what to do next through `django_todo` (soft dependency: `services/task_bridge.py` is
the only service that imports it; without it every call is a no-op and nothing else changes). A task is about the
company (`subject_ref = leads.Company:<id>`, `subject_label` = its name), names the contact when one is known
(`contact_ref = leads.Contact:<id>`), belongs to the company's owner (none or deactivated → unowned) and has
`created_by = "reply"`. One reply creates at most one task — first match:

| Reply | Task | Due | `source` / `dedup_key` |
|---|---|---|---|
| `needs_review`, or no class | `todo` "Read the reply" | now | `leads.reply.review` / `reply:<reply id>` |
| `interested` | `todo` "Interested: answer and open a deal", note "Open a deal on this company" (no deal is opened automatically) | now | `leads.reply.interested` / `reply:<reply id>` |
| `not_now` | `mail` "Come back: <contact>" | the day after `intent_return_date`, else now + `resume_days["not_now"]` | `leads.reply.not_now` / `resume:<contact id>` |
| `out_of_office` without a return date | `mail` "Try again: <contact>" | now + `resume_days["out_of_office"]` | `leads.reply.out_of_office` / `resume:<contact id>` |
| `wrong_person` | `todo` "Find the right person" | now | `leads.reply.wrong_person` / `wrong:<contact id>` |

No task for `unsubscribe` (the suspected opt-out path already asks a person), `not_interested`, `unclear`, an
absence with a return date (communicator resumes the sequence) or a class about a contact the reply cannot be matched
to. `Channel.resume_days` (JSON, `PATCH channel/`) overrides the defaults 30 (`not_now`) and 7 (`out_of_office`).
A newer reply of the contact cancels its open `resume:<contact id>` task (outcome "newer reply"), and so do an
opt-out ("opted out") and an anonymisation ("anonymised"). Nothing is drafted when a "come back" task is due: the
person decides. The task is created after the reply commits; a failure is logged and the reply stays recorded.

Manual tasks have no leads endpoint: the CMS uses the todo API with `subject_ref = leads.Company:<id>` or
`leads.Deal:<id>` (written on the deal's company with `Activity.deal` set). Leads writes
the timeline: Activity `task` "task: <title>" when a person creates one (tasks with a `leads.` source write none —
the reply activity says why), "done: <title> — <outcome>" or "cancelled: <title>" when it closes, with
`data = {task_id, kind, status}` and the task's contact when `contact_ref` resolves to a contact of the company.

## Writing by hand

`POST companies/<id>/compose/` → `outreach_service.compose_message`: the locks and the outreach gate of
`request_draft` (a refusal → one Activity `blocked: <reason>`, 409 `not_eligible`), the legal footer of the contact's
basis and language (none → 409 `no_legal_basis`), then communicator's `compose_service.compose` from the owner's
mailbox (else the channel default), approved by its author and due now; it stops the thread's sequence. Activity
`draft` "written by hand" with the message id; the `message_sent` receiver records the send. With `reply_id` the
message answers that reply (`In-Reply-To`, its thread); the reply must belong to a thread of this company and of the
contact.

## Integration

| Module | leads calls | leads receives |
|---|---|---|
| communicator | `compose(channel_idx, recipient, subject, body_text, subject_ref, author, mailbox_user_id, in_reply_to)` for a hand-written mail; `communicate(channel_idx, template_key, recipient, context, subject_ref="leads.Company:<id>", requires_review=True, audience=<lead_type>, mailbox_user_id=<owner id>)` — the only caller from leads; leads never writes a `Message` | `reply_received` → Activity `reply` with the class, wrong-person flag, `reply:<class>` map event, notification of the reply plan's severity (`none` = none); `company_skipped` (reviewer) → `set_do_not_contact`, Activity `blocked`; `message_sent` → Activity `sent`, the first one `first_sent`; `sequence_finished` → map event, rotation task; `optout_confirmed` → `opt_out`; `message_bounced` (hard) → Activity `note` "address invalid: hard bounce". Calls `suppression_service` `suppress_email` / `create_suppression` / `delete_suppression` / `can_mail` / `explain` / `scope` |
| agreements | `resolve_clause_set(channel_idx, legal_basis, language)` + `render_legal_footer` (contact language, else channel default); `LegalBasis` enum | — (a missing clause set → Activity `skipped: no clause set`, no draft) |
| siteintel | `request_audit(domain, channel_idx, requested_by)` | `report_ready(audit, succeeded_sources)` → analysis task |
| toolbox (`django_utils.toolbox`) | one completion per analysis (`AnalysisProfile` `leads.analysis`) and per `ai_pick` | — |
| notifications (soft) | `notify(channel_idx, recipient_role=LEADS_NOTIFY_ROLE, severity, subject_ref, title, body, recipient_user_id=<owner id or null>)` — the owner only when the company has one | — |
| contact_forms (soft) | reads `Lead`, never writes it | `lead_status_changed` on creation |
| todo (soft) | `task_service.create` / `cancel` / `scrub`, `Task` reads for the GDPR export and the open-task counts of deals — only in `services/task_bridge.py` | `task_created`, `task_closed` → Activity `task` (§ Tasks), also for `leads.Deal:<id>` |
| accounts (soft) | verified `EmailAddress` → `Customer.uid` on `create-customer/` | — |

Emitted: `stage_entered(company, stage)` (own receiver → `evaluate_rules` task), `deal_stage_changed(deal, previous,
stage)` (on commit of a real deal stage change; no own receiver) and
`contact_anonymised(email_hash, anonymised_email, subject_ref)` — communicator suppresses the token on every
channel and anonymises its threads. All receivers use `dispatch_uid="django_leads.<name>"`, run after commit, never
raise into the sender, and handle only `subject_ref` `leads.Company:<int>` (and, for tasks, `leads.Deal:<int>`).

### Intel analysis

`report_ready` → task `analyse_intel` → claim `intel:<audit>:<task id>` (a redelivered message keeps its task id and
finds the claim) → one toolbox completion, never retried → `hooks` (≤ `LEADS_ANALYSIS_MAX_HOOKS`), `platform`, and
`lead_type` when it was `UNKNOWN` — only a `lead_type_guess` (or the pre-0.3 `company_type_guess`) that is an
active lead type of the channel; the prompt gets the channel's list as `{lead_types}` → Activity `intel` → `intel_ready` rules. No succeeded source → no toolbox
call, hooks cleared, Activity `intel_empty`, then the rules (never on stale hooks). A failure → Activity
`analysis failed: <code>` + medium notification, no rules. A toolbox switched off on purpose
(`AI_TOOLBOX_ENABLED = False`) is not a failure: Activity `analysis skipped: AI toolbox disabled`, claim `retry`
with no failure counted, no notification, no rules; the analysis runs once the switch is on.

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
`LEADS_ROTATION_MAX_FAILURES` → `rotation_gave_up`, claim `failed`. A draft refused because the toolbox is switched
off → Activity `skipped: AI toolbox disabled`, claim `retry` with no failure counted.

## GDPR and retention

Rows are never deleted. Retention and erasure pseudonymise a contact: email → deterministic token
`anon-<sha256[:16]>@LEADS_ANONYMISED_DOMAIN`, names, job title, phone and contact attributes cleared, `anonymised_at` set, token kept in
`ErasedAddress`, Activity `anonymised` with `email_hash`, `contact_anonymised` on commit — once, under a row lock.
Company (and its attributes — company data), stage, hooks, counters and timeline stay. The GDPR export lists the
contact attributes with the contact. An erased address is never re-imported (`erased_address`) nor
re-created from a form.

A deal is company data: anonymising (retention, erasure) or removing a contact only takes it off its deals
(`Deal.contact` → null; `SET_NULL` on removal). Nothing on a deal is scrubbed, so deal titles and lost reasons must not carry personal
data (`gotchas.md`).

GDPR hooks: every installed app's top-level `<app>.gdpr` module with `gdpr_export(email) -> dict` and
`gdpr_erase(email) -> dict[str, int]` (`gdpr/protocol.GdprHooks`) is discovered at call time. Erasure runs in one
transaction after a `note` "gdpr erase requested" on every affected company; leads' own hook anonymises the contacts
(actor `gdpr`), erases the free text of their tasks (`task_service.scrub(contact_ref=…)`, also on retention) and scrubs their Activities to `[erased]`; the export lists the contact's tasks (`id, title, status, due_at`) under `Task`. Matching covers the address and its token.

`utils/emails.email_hash` / `anonymised_address` are canonical; communicator and agreements keep local copies (no
dependency on leads) pinned by parity tests. Operating it: `operations.md`.
