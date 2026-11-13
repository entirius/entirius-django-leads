---
title: Admin API
description: Every admin API v2 endpoint — method, path, parameters, response shape, errors.
---

All endpoints live under `/api/leads/v2/admin/` (the host includes `django_leads.urls`). The machine-readable
contract is `docs/openapi.yaml` (generated from the service, filtered to `/api/leads/`); this page adds what the
schema cannot say. Views are thin: parse into a Pydantic schema (`schemas/requests.py`), call a service, dump a
response schema (`schemas/responses.py`). Field whitelists live in the services.

## Auth, scoping, paging

| | |
|---|---|
| Authentication | `JWTAuthentication`, declared on every view (never inherited from the host default) |
| Permission | `IsAdminUser` — a customer JWT is a 403 |
| Channel | `<channel_idx>` is `Channel.idx`; an unknown idx is a 404. A row id of another channel is a 404, never a leak |
| Paged lists | `?page=&page_size=` (default 20, max 100) → `{count, next, previous, results}` |
| Unpaged lists | stages, rules, analysis and recipient profiles → `{results}` |

## Companies

| Method | Path | Body / params | Success |
|---|---|---|---|
| GET | `<channel_idx>/companies/` | `stage` (key), `search` (substring of name or domain), `sort` (`name`, `domain`, `stage_entered_at`, `last_activity_at`, `score_points` — never-scored companies last either way, `owner__username`; `-` prefix; default `name`), `lead_type` (an active lead type code of the channel or `UNKNOWN`; anything else is a 400), `do_not_contact` (bool), `has_reply` (bool: at least one `reply` Activity), `origin` (exact), `segment` (`S1`–`S4`), `attr.<key>` (exact value of an active company attribute with `show_in_list` of kind `select`, `bool` or `text`; any other key is a 400), `owner` (`me` = the caller, `none` = unowned, a user id), paging | 200 `CompanyListResponse` |
| POST | `<channel_idx>/companies/` | `domain` (required, domain or URL, stored registrable), `name` (default: the domain), `website`, `lead_type` (active code or `UNKNOWN`, default `UNKNOWN`), `industry`, `attributes` (every `required` company attribute must be given), `owner_id` (absent = the requesting user, null = no owner) | 201 `CompanyDetailResponse`; Activity `note` "company created" (+ "owner: <username>") |
| GET | `<channel_idx>/companies/<id>/` | — | 200 `CompanyDetailResponse` |
| PATCH | `<channel_idx>/companies/<id>/` | any of `name`, `website` (null clears), `lead_type` (active code or `UNKNOWN`), `industry`, `description`, `do_not_contact` (on: the domain is suppressed in communicator; off: that suppression is removed), `external_ref`, `origin` (≤ 64), `attributes` (merged: given keys are set, a `null` key is removed; a required key may not be removed), `owner_id` (null clears) | 200 `CompanyDetailResponse`; an owner change is an Activity `note` "owner: <username \| none>" |
| POST | `<channel_idx>/companies/assign/` | `company_ids` (1–200 companies of the channel), `owner_id` (null clears) | 200 `{updated}` (companies whose owner changed); one Activity `note` per change; more than 200 ids or an id of another channel is a 400 on `company_ids` |
| GET | `<channel_idx>/assignable-users/` | `search` (substring of username, first or last name) | 200 `{results: [{id, username, name}]}` — active staff by name, at most 50; nothing else about a user |
| POST | `<channel_idx>/companies/<id>/transition/` | `stage_key` | 200 `CompanyDetailResponse`; Activity `stage`, `stage_entered` on commit (same stage: no-op) |
| POST | `<channel_idx>/companies/<id>/score/` | — | 200 `ScoreResponse` `{segment, points, disqualified, reasons, reasons_structured: [{field, kind, points, label}], profile_id}`; stores `score_points`, `score_segment`, `scored_at`; Activity `score` with the reasons |
| POST | `<channel_idx>/companies/<id>/communicate/` | `template_key`, `contact_id` | 201 `{message_id, status}` — a reviewable communicator draft |
| GET | `<channel_idx>/companies/<id>/contacts/<contact_id>/mailable/` | — | 200 `MailableResponse` |
| POST | `<channel_idx>/companies/<id>/request-audit/` | — | 202 `{audit_id, status}`; Activity `intel` "audit requested" |
| POST | `<channel_idx>/companies/<id>/create-customer/` | — (routed only with `django_accounts`) | 200 `{customer_uid}` |

`CompanyResponse`: `id, name, domain, website, lead_type, industry, description, platform, hooks, stage
{id, key, label, order, kind, is_terminal}, stage_entered_at, source, external_ref, do_not_contact,
customer_uid, rotation_count, last_activity_at, origin, attributes, score_points (null = never scored), score_segment
("" = never scored), scored_at, owner {id, username, name} | null`. `CompanyDetailResponse` adds `contacts` (all), `activities`
(last 20, newest first) and `customer_name` (the linked customer's name, `""` without `django_accounts` or when
the customer is gone — the uid stays the reference). `stage`, `domain`, `hooks`, the score fields and `customer_uid`
are not writable here. A refused attribute (unknown or inactive key, a value its kind refuses, a missing required
key) is a 400 on `attributes`. An `owner_id` that is not an active staff user (or is a service user of an access
application) is a 400 on `owner_id`, here, on `assign/` and on `imports/`.

`communicate/` skips rule conditions and cooldown, not the outreach gate (`concept.md` § Outreach gate).
`create-customer/` links the Customer owning the primary contact's email through a **verified** allauth
`EmailAddress`; v1 never creates an account.

`MailableResponse` (a read; the gate itself runs only when a draft is requested): `eligible`, `reasons` (every
refusal in gate order: `do_not_contact`, `no_email`, `opted_out`, `anonymised`, `wrong_person`, `no_legal_basis`,
then communicator's `suppressed` / `address_invalid`), `legal_basis {value, source, set_at}` (source and time of the
last `legal_basis` Activity, null when never recorded), `communicator` (null without an email or a communicator
channel: `{email, refusal, suppressions: [{id, kind, source, reason, scope, created_at}], address_status {status,
reason, verified_at} | null}`), `retention {days, due_at, exempt_won}` (`due_at` = last activity + days).

## Contacts

| Method | Path | Body / params | Success |
|---|---|---|---|
| GET | `<channel_idx>/contacts/` | `company` (id), paging | 200 `ContactListResponse` |
| POST | `<channel_idx>/contacts/` | `company_id` (required), `email`, `first_name`, `last_name`, `job_title`, `phone`, `language` (ISO 639-1), `is_primary`, `legal_basis`, `consent_ref`, `attributes` (every `required` contact attribute must be given) | 201 `ContactResponse` |
| GET | `<channel_idx>/contacts/<id>/` | — | 200 `ContactResponse` |
| PATCH | `<channel_idx>/contacts/<id>/` | any of `email` (only while the contact has none), `first_name`, `last_name`, `job_title`, `phone`, `language` (null clears), `is_primary`, `legal_basis` (null clears), `consent_ref`, `attributes` (merged; a `null` key is removed) | 200 `ContactResponse` |
| DELETE | `<channel_idx>/contacts/<id>/` | — | 204 — the contact was never used and is deleted; 200 `ContactResponse` — it was used and is anonymised instead |

`ContactResponse`: `id, company_id, email, first_name, last_name, job_title, phone, language, is_primary, source,
legal_basis, opt_out_at, anonymised_at, wrong_person_at, attributes`. Contact attributes are personal data: GDPR
export includes them, anonymisation and erasure clear them. `email` is set once: a contact without one may get it by PATCH (409
`CONTACT_EXISTS` when the company already has it), a set email never changes (400). `legal_basis` is one of
`consent`, `legitimate_interest`, `contract`; `consent` requires a non-blank `consent_ref` (≤ 255), recorded in the
`legal_basis` Activity as `admin:<user>:<consent_ref>`; other bases as `admin:<user>`.

A company has at most one primary contact: `is_primary: true` on create or PATCH unsets the others. Removing the
primary promotes nobody — the rules then take the first eligible contact by id.

DELETE decides by use (`contact_service.is_used`): a contact that opted out, has a recorded consent (`legal_basis`
`consent` or a `consent_ref`), an outreach Activity (draft, sent, reply, bounce, opt-out) or a communicator thread to its
email under the company's `subject_ref` is anonymised like retention does it (`anonymised_at` set, personal fields
blanked, timeline and thread kept, never a recipient again); any other contact is deleted and a `contact removed` note
stays on the timeline. Removing an anonymised contact answers 200 and changes nothing.

## Stages

| Method | Path | Body | Success |
|---|---|---|---|
| GET | `<channel_idx>/stages/` | — | 200 `{results: [StageResponse]}` in `order` |
| POST | `<channel_idx>/stages/` | `key` (slug, required), `label` (required), `order` (0–32767), `kind` (`open`, `won`, `lost`, `unresponsive`), `is_terminal` | 201 `StageResponse` |
| GET | `<channel_idx>/stages/<id>/` | — | 200 `StageResponse` |
| PATCH | `<channel_idx>/stages/<id>/` | any field of POST, no nulls | 200 `StageResponse` |
| DELETE | `<channel_idx>/stages/<id>/` | — | 204 |

## Stage events

The event → stage map (`concept.md` § Event → stage map); area `leads.settings`. `<event>` is one of `first_sent`,
`reply`, `reply:interested`, `reply:not_now`, `reply:not_interested`, `reply:out_of_office`, `reply:wrong_person`,
`reply:unsubscribe`, `reply:unclear`, `sequence_finished`; any other is a 404.

| Method | Path | Body | Success |
|---|---|---|---|
| GET | `<channel_idx>/stage-events/` | — | 200 `{results: [StageEventResponse]}`, one per event |
| PUT | `<channel_idx>/stage-events/<event>/` | `target_stage_key` (required; null = keep the stage), `forward_only` (default true) | 200 `StageEventResponse`; an unknown stage key is a 400 |
| DELETE | `<channel_idx>/stage-events/<event>/` | — | 204 — the event moves nothing any more |

`StageEventResponse`: `event, target_stage_key, forward_only, configured` (false = no row: the event moves nothing).

## Attributes and fields

A channel defines its own fields for companies, contacts and deals (`deal` is accepted ahead of the deals model).
Values live in the entity's `attributes` and are written only through company / contact create and patch, and the
CSV import. Values are stored normalised: `number` → decimal string (at most 30 digits), `date` → ISO date, `bool` → bool, `select` → one
listed value, `multiselect` → a list of listed values, `url` → http(s) URL, `text` → at most 2 000 characters, never
interpreted as HTML.

| Method | Path | Body / params | Success |
|---|---|---|---|
| GET | `<channel_idx>/attributes/` | `entity` (`company`, `contact`, `deal`) | 200 `{results: [AttributeDefinitionResponse]}` by entity, then `order` |
| POST | `<channel_idx>/attributes/` | `entity`, `key` (`^[a-z][a-z0-9_]*$`, ≤ 48, not a built-in fact name), `label`, `kind` (`text`, `number`, `bool`, `date`, `select`, `multiselect`, `url`), `options` (`[{value, label}]`, required for the select kinds, refused for the others), `required`, `order`, `show_in_list`, `is_active` | 201 `AttributeDefinitionResponse` |
| GET | `<channel_idx>/attributes/<id>/` | — | 200 `AttributeDefinitionResponse` |
| PATCH | `<channel_idx>/attributes/<id>/` | any of `label`, `kind` (409 once a value is stored or a condition names it), `options`, `required`, `order`, `show_in_list`, `is_active`, no nulls — entity and key are fixed | 200 `AttributeDefinitionResponse` |
| DELETE | `<channel_idx>/attributes/<id>/` | — | 204; 409 while values are stored (deactivate instead) |
| GET | `<channel_idx>/fields/` | — | 200 `{results: [{name, kind, label, options}]}` |

`required` is enforced on create and on a patch that touches the key, never retroactively. An inactive definition
keeps its stored values but is refused in new values, conditions, imports and list filters.

`fields/` lists every fact a rule condition or a scoring rule may name: the built-ins `lead_type` (select of the
active lead types and `UNKNOWN`), `industry`, `platform`, `domain`, `origin` (text), `stage` (select of the stage
keys), `hooks_count`, `emails_count`, `rotation_count`, `score_points` (number), `score_segment` (select `S1`–`S4`),
then `attr.<key>` for every active company attribute. Area `leads.settings`.

A scoring profile PATCH re-checks only the fields it carries: a profile whose attribute was deactivated can still be
switched off, while re-saving its rules is refused until they name known fields. A condition whose stored value no
longer fits the fact never matches (a definition named by a condition keeps its kind).

## Scoring profiles

| Method | Path | Body | Success |
|---|---|---|---|
| GET | `<channel_idx>/scoring-profiles/` | — | 200 `{results: [ScoringProfileResponse]}` by key |
| POST | `<channel_idx>/scoring-profiles/` | `key` (default `leads.scoring`), `thresholds` `{S, M, L}` (integers, S ≤ M ≤ L), `rules` `[{field, op, value, points, label}]`, `disqualifiers` `[{field, op, value, label}]`, `is_active` | 201 `ScoringProfileResponse` |
| GET / PATCH / DELETE | `<channel_idx>/scoring-profiles/<id>/` | PATCH: any field of POST, no nulls | 200 / 200 / 204 |

The first active profile by key scores the channel. Points ≥ L → `S1`, ≥ M → `S2`, ≥ S → `S3`, else `S4`; a matching
disqualifier forces `S4`. Rules and disqualifiers are conditions (§ Rules) — an unknown field or an op its kind does
not take is a 400 at save.

## Lead types

The kinds of lead a channel works with — configuration, not code. `Company.lead_type` holds the `code`; `UNKNOWN`
is built in, never a row, always valid. An inactive type still reads on its companies but is not accepted by the
filter, create/patch, the import (reads as `UNKNOWN`) or the intel guess. A communicator template targets a lead
type through its `audience` (cascade in `concept.md` § Outreach).

| Method | Path | Body | Success |
|---|---|---|---|
| GET | `<channel_idx>/lead-types/` | — | 200 `{results: [LeadTypeResponse]}` in `order`, inactive ones included |
| POST | `<channel_idx>/lead-types/` | `code` (upper-case `A-Z0-9_`, required), `label` (required), `order` (0–32767), `is_active` | 201 `LeadTypeResponse` |
| GET | `<channel_idx>/lead-types/<id>/` | — | 200 `LeadTypeResponse` |
| PATCH | `<channel_idx>/lead-types/<id>/` | any of `label`, `order`, `is_active`, no nulls — the code is fixed | 200 `LeadTypeResponse` |
| DELETE | `<channel_idx>/lead-types/<id>/` | — | 204 |

`LeadTypeResponse`: `id, code, label, order, is_active`.

## Timeline and imports

| Method | Path | Body / params | Success |
|---|---|---|---|
| GET | `<channel_idx>/activities/` | `company` (id), paging | 200 `ActivityListResponse` — `id, company_id, contact_id, kind, message, data, actor, created_at`, newest first |
| GET | `<channel_idx>/imports/` | paging | 200 `ImportBatchListResponse`, newest first, without `report` |
| POST | `<channel_idx>/imports/` | `multipart/form-data` field `file`: UTF-8 CSV (BOM allowed), ≤ 20 MB; `run_rules` (`true` / `false`, default `false`); `owner_id` (optional) | 202 `ImportBatchResponse` (`pending`); the run is queued after commit |
| GET | `<channel_idx>/imports/<id>/` | — | 200 `ImportBatchDetailResponse` |

`ImportBatchResponse`: `id, source, filename, status (pending|running|done|failed), created_count, matched_count,
skipped_count, row_count, size_bytes, created_by, created_at, run_rules, owner_id, rules_queued` (`rules_queued`: one rule
evaluation per created company when `run_rules` is on, else 0). The detail adds `report: [{row, reason}]` — CSV line
(header = 1) and reason code, never a row value; `row: 0` is a file-level failure. Codes: `operations.md` § Import.

## Reports

| Method | Path | Params | Success |
|---|---|---|---|
| GET | `<channel_idx>/reports/funnel/` | `origin` (exact), `date_from`, `date_to` (`YYYY-MM-DD`, inclusive) | 200 `FunnelResponse` |

`FunnelResponse` (area `leads.companies`), aggregated from `stage` activities in four queries: `transitions:
[{from_stage, to_stage, count, mean_seconds}]` (moves in the period, mean time in the source stage — from the
previous move or the company's creation), `stages: [{key, label, current, entered, left, mean_seconds}]` in pipeline
order (`current` = companies there now, of the origin; `mean_seconds` over the moves out), `origins: [{origin,
companies}]` (companies created in the period). Dates filter moves and creations by their local day.

## Rules and prompt profiles

| Method | Path | Body / params | Success |
|---|---|---|---|
| GET | `<channel_idx>/rules/` | — | 200 `{results: [RuleResponse]}` in evaluation order |
| POST | `<channel_idx>/rules/` | `trigger` (`stage_entered`, `intel_ready`; required), `stage_id` (required for `stage_entered`), `action` (`communicate` default, `request_audit`, `score`), `template_key` (required for `communicate`), `contact_strategy` (`primary`, `ai_pick`), `require_hooks` (true), `require_email` (true), `cooldown_hours` (24, 0–8760), `is_active`, `order`, `conditions` (`[{field, op, value}]`, all must hold) | 201 `RuleResponse` |
| GET / PATCH / DELETE | `<channel_idx>/rules/<id>/` | PATCH: any field of POST, null only for `stage_id` | 200 / 200 / 204 (runs deleted with the rule) |
| GET | `<channel_idx>/rule-runs/` | `company` (id), paging | 200 `RuleRunListResponse` — `id, rule_id, company_id, fired_at, outcome (fired|skipped|blocked|cooldown, empty while claimed), detail` |
| GET | `<channel_idx>/analysis-profiles/` | — | 200 `{results: [{id, key, model, is_active}]}` |
| POST | `<channel_idx>/analysis-profiles/` | `key` (default use: `leads.analysis`), `prompt_text`, `json_schema`, `model`, `is_active` | 201 profile with `prompt_text`, `json_schema` |
| GET / PATCH / DELETE | `<channel_idx>/analysis-profiles/<id>/` | PATCH: any field, no nulls | 200 / 200 / 204 |
| GET | `<channel_idx>/recipient-profiles/` | — | 200 `{results: [{id, key, model}]}` |
| POST | `<channel_idx>/recipient-profiles/` | `key` (default use: `leads.pick_recipient`), `prompt_text`, `json_schema`, `model` | 201 profile with `prompt_text`, `json_schema` |
| GET / PATCH / DELETE | `<channel_idx>/recipient-profiles/<id>/` | PATCH: any field, no nulls | 200 / 200 / 204 |

Lists never carry `prompt_text`. `require_legal_basis` exists on the model but is not exposed and has no effect.

**Conditions** (`StageRule.conditions`, scoring rules and disqualifiers): `field` is a name from `fields/`, `op` one
its kind takes, `value` normalised like an attribute value of that kind; checked at save (400), never at run time.

| Kind | Ops |
|---|---|
| `text`, `url` | `eq`, `ne`, `in`, `contains` (case-insensitive), `empty` |
| `number` | `eq`, `ne`, `in`, `gte`, `lte`, `empty` |
| `date` | `eq`, `ne`, `gte`, `lte`, `empty` |
| `bool` | `eq`, `ne`, `empty` |
| `select` | `eq`, `ne`, `in`, `empty` |
| `multiselect` | `contains` (one listed value), `empty` |

`in` takes a non-empty list; `empty` takes a bool (default true: the fact must be empty; false: it must hold a
value). A fact without a value satisfies only `ne` and `empty`. Text `eq` / `ne` / `in` compare case-insensitively.

## GDPR (channel-independent)

| Method | Path | Body | Success |
|---|---|---|---|
| POST | `gdpr/export/` | `email` (Django `validate_email`, so `.test` addresses pass) | 200 `{email, generated_at, modules: {app: {Model: [rows]}}}` |
| POST | `gdpr/erase/` | `email` | 200 `{modules: {app: {kind: count}}}` — irreversible |

Both span every installed app with a `gdpr` hooks module (`concept.md` § GDPR). No 404.

## Development endpoints

Routed and answering only with `ENVIRONMENT == "development"` (404 otherwise). Used by the BDD suite.

| Method | Path | Body | Success |
|---|---|---|---|
| POST | `<channel_idx>/test/import-now/` | multipart `file`, as `imports/` | 200 `ImportBatchDetailResponse` — imported in the request, no worker or shared temp dir |
| POST | `<channel_idx>/test/evaluate/` | `company_id`, `trigger`, `stage_key` (default: the company's stage) | 200 `{runs: [RuleRunResponse]}` — no event key, so no redelivery dedupe |
| POST | `<channel_idx>/test/rotate-now/` | — | 200 `{rotated}` — the daily rotation scan, this channel only |
| POST | `<channel_idx>/test/retry-analyses/` | — | 200 `{recovered, failed}` — the intel analysis retry in-process, **every** channel |
| POST | `<channel_idx>/test/anonymise-now/` | `as_of` (aware datetime, optional) | 200 `{anonymised: {channel_idx: count}}` — the retention task in-process, **every** channel |

## Errors

Validation and DRF errors go through the host's v2 exception handler:
`{error, message, debug_id, details: [{field, location, issue, description}]}`.

| Status | Cause |
|---|---|
| 400 | schema validation (unknown field, bad enum, `sort` outside the allowlist, `consent` without `consent_ref`, null where not allowed); unknown `language`; missing, oversized or non-UTF-8 upload; `contact_id` not a contact with an email in the company; rule `clean()` (stage required / from another channel, template required); a condition, scoring rule or disqualifier on an unknown field or with an op its kind does not take; thresholds out of order; an attribute definition with a built-in key or bad options; a refused attribute value, filter or missing required attribute (`attributes`) |
| 401 / 403 | no or invalid JWT / not staff |
| 404 | unknown channel, row of another channel, unknown `stage_key`, development endpoint outside development |
| 409 | one of the codes below |
| 503 | `communicate/` with an AI template while the toolbox is switched off (`AI_TOOLBOX_ENABLED = False`): body `{error: "AI_TOOLBOX_DISABLED", message}`, no draft, Activity `skipped: AI toolbox disabled` |

Every 409 uses the same v2 shape, `{error, message, debug_id, details}` — `error` is the code below,
upper-cased; `message` is human-readable; `details` is empty for these (no field-level errors).

| Code (`error`) | Raised by |
|---|---|
| `STAGE_EXISTS` | `POST`/`PATCH stages/`: another stage of the channel already has this key |
| `STAGE_NOT_EMPTY` | `DELETE stages/<pk>/`: the stage still holds companies, or a stage event moves companies to it |
| `LEAD_TYPE_EXISTS` | `POST lead-types/`: the channel already has this code, or the code is the built-in `UNKNOWN` |
| `LEAD_TYPE_IN_USE` | `DELETE lead-types/<pk>/`: companies of the channel still carry the code — retype them or deactivate the type |
| `DOMAIN_EXISTS` | `POST companies/`: another company of the channel already has this registrable domain |
| `NO_STAGES` | `POST companies/`: the channel has no pipeline yet |
| `CONTACT_EXISTS` | `POST contacts/`, `PATCH contacts/<id>/` (`email`): the company already has a contact with this email |
| `PROFILE_EXISTS` | `POST`/`PATCH {analysis,recipient,scoring}-profiles/`: another profile of the channel already has this key |
| `ATTRIBUTE_EXISTS` | `POST attributes/`: the channel already defines this key for the entity |
| `ATTRIBUTE_IN_USE` | `PATCH attributes/<pk>/` (`kind`), `DELETE attributes/<pk>/`: values are stored, or a rule condition / scoring rule names the attribute — deactivate instead |
| `NO_SCORING_PROFILE` | `POST companies/<id>/score/`: the channel has no active scoring profile |
| `COMMUNICATOR_CHANNEL_MISSING` | `communicate/`: no communicator channel is configured |
| `NOT_ELIGIBLE` | `communicate/`: the outreach gate refused the contact (`do_not_contact`, `opted_out`, `anonymised`, `wrong_person`, `no_legal_basis`, `suppressed`, `address_invalid`) |
| `NO_DRAFT` | `communicate/`: no draft could be built (no clause set) — see the company timeline |
| `NOT_IMPLEMENTED` | `create-customer/`: no Customer owns the primary contact's email — v1 never creates accounts |
