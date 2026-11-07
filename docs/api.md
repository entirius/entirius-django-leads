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
| Unpaged lists | stages, rules, analysis, recipient and scoring profiles → `{results}` |

## Companies

| Method | Path | Body / params | Success |
|---|---|---|---|
| GET | `<channel_idx>/companies/` | `stage` (key), `search` (substring of name or domain), `sort` (`name`, `domain`, `stage_entered_at`, `last_activity_at`, `-` prefix; default `name`), `lead_type` (an active lead type code of the channel or `UNKNOWN`; anything else is a 400), `do_not_contact` (bool), `origin` (exact), `has_reply` (bool: at least one `reply` Activity), paging | 200 `CompanyListResponse` |
| POST | `<channel_idx>/companies/` | `domain` (required, domain or URL, stored registrable), `name` (default: the domain), `website`, `lead_type` (active code or `UNKNOWN`, default `UNKNOWN`), `industry`, `origin` (max 64) | 201 `CompanyDetailResponse`; Activity `note` "company created" |
| GET | `<channel_idx>/companies/<id>/` | — | 200 `CompanyDetailResponse` |
| PATCH | `<channel_idx>/companies/<id>/` | any of `name`, `website` (null clears), `lead_type` (active code or `UNKNOWN`), `industry`, `origin`, `description`, `do_not_contact`, `external_ref` | 200 `CompanyDetailResponse` |
| POST | `<channel_idx>/companies/<id>/transition/` | `stage_key` | 200 `CompanyDetailResponse`; Activity `stage`, `stage_entered` on commit (same stage: no-op) |
| POST | `<channel_idx>/companies/<id>/communicate/` | `template_key`, `contact_id` | 201 `{message_id, status}` — a reviewable communicator draft |
| POST | `<channel_idx>/companies/<id>/request-audit/` | — | 202 `{audit_id, status}`; Activity `intel` "audit requested" |
| POST | `<channel_idx>/companies/<id>/create-customer/` | — (routed only with `django_accounts`) | 200 `{customer_uid}` |

`CompanyResponse`: `id, name, domain, website, lead_type, industry, description, platform, hooks, stage
{id, key, label, order, kind, is_terminal, on_reply, on_sent}, stage_entered_at, source, external_ref, do_not_contact,
customer_uid, rotation_count, last_activity_at`. `CompanyDetailResponse` adds `contacts` (all), `activities`
(last 20, newest first) and `customer_name` (the linked customer's name, `""` without `django_accounts` or when
the customer is gone — the uid stays the reference). `stage`, `domain`, `hooks` and `customer_uid` are not
writable here.

`communicate/` skips rule conditions and cooldown, not the outreach gate (`concept.md` § Outreach gate).
`create-customer/` links the Customer owning the primary contact's email through a **verified** allauth
`EmailAddress`; v1 never creates an account.

## Contacts

| Method | Path | Body / params | Success |
|---|---|---|---|
| GET | `<channel_idx>/contacts/` | `company` (id), paging | 200 `ContactListResponse` |
| POST | `<channel_idx>/contacts/` | `company_id` (required), `email`, `first_name`, `last_name`, `job_title`, `phone`, `language` (ISO 639-1), `is_primary`, `legal_basis`, `consent_ref` | 201 `ContactResponse` |
| GET | `<channel_idx>/contacts/<id>/` | — | 200 `ContactResponse` |
| PATCH | `<channel_idx>/contacts/<id>/` | any of `email` (only while the contact has none), `first_name`, `last_name`, `job_title`, `phone`, `language` (null clears), `is_primary`, `legal_basis` (null clears), `consent_ref` | 200 `ContactResponse` |
| DELETE | `<channel_idx>/contacts/<id>/` | — | 204 — the contact was never used and is deleted; 200 `ContactResponse` — it was used and is anonymised instead |

`ContactResponse`: `id, company_id, email, first_name, last_name, job_title, phone, language, is_primary, source,
legal_basis, opt_out_at, anonymised_at`. `email` is set once: a contact without one may get it by PATCH (409
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
| POST | `<channel_idx>/stages/` | `key` (slug, required), `label` (required), `order` (0–32767), `kind` (`open`, `won`, `lost`, `unresponsive`), `is_terminal`, `on_reply`, `on_sent` | 201 `StageResponse` |
| GET | `<channel_idx>/stages/<id>/` | — | 200 `StageResponse` |
| PATCH | `<channel_idx>/stages/<id>/` | any field of POST, no nulls | 200 `StageResponse` |
| DELETE | `<channel_idx>/stages/<id>/` | — | 204 |

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
| POST | `<channel_idx>/imports/` | `multipart/form-data` field `file`: UTF-8 CSV (BOM allowed), ≤ 20 MB | 202 `ImportBatchResponse` (`pending`); the run is queued after commit |
| GET | `<channel_idx>/imports/<id>/` | — | 200 `ImportBatchDetailResponse` |

`ImportBatchResponse`: `id, source, filename, status (pending|running|done|failed), created_count, matched_count,
skipped_count, row_count, size_bytes, created_by, created_at`. The detail adds `report: [{row, reason}]` — CSV line
(header = 1) and reason code, never a row value; `row: 0` is a file-level failure. Codes: `operations.md` § Import.

## Funnel report

| Method | Path | Body / params | Success |
|---|---|---|---|
| GET | `<channel_idx>/reports/funnel/` | `origin` (exact; empty value = companies without one; omitted = any), `date_from`, `date_to` (ISO 8601 with offset; 400 when reversed or malformed) | 200 `FunnelResponse` |

`FunnelResponse`: `companies`; `transitions: [{from_stage, to_stage, count, avg_seconds_in_from_stage}]` most frequent
first; `stages: [{stage, current, entered, left, avg_seconds_in_stage}]`; `origins: [{origin, companies}]` (whole
channel, never filtered). Built from Activity `stage` rows: time in a stage runs from the previous entry (or the
company's creation for the first stage) to the transition that leaves it; the date window bounds the transition time
only; companies still in a stage count in `current` but not in the averages. `Company.origin` (free text, <= 64) is the
business origin (`ecom.house`, a partner); it is distinct from `source` (csv, form, manual, connector). It is set on
`POST`/`PATCH companies/` and by the CSV column `origin` (fills an empty value only), and filters `GET companies/`.

## Pilots

A pilot is a trial run with a company: dates, success criterion, outcome and the day it moves to a licence (S5).

| Method | Path | Body / params | Success |
|---|---|---|---|
| GET | `<channel_idx>/pilots/` | `company` (id), `result` (`pending|success|failure|cancelled`), paging | 200 `PilotListResponse`, newest start first |
| POST | `<channel_idx>/pilots/` | `company_id` (company of the channel, else 404), `start_date` (required), `end_date`, `success_criterion`, `result` (default `pending`), `result_note`, `license_switch_date` | 201 `PilotResponse`; Activity `note` "pilot started <date>"; 400 when `end_date` or `license_switch_date` precede `start_date` |
| GET / PATCH / DELETE | `<channel_idx>/pilots/<id>/` | PATCH: any field except `company_id`; null only for `end_date`, `license_switch_date` | 200 / 200 / 204; a changed `result` writes Activity `note` "pilot <old> -> <new>" |
| GET | `<channel_idx>/pilots/report/` | `as_of` (date, default today) | 200 `{as_of, total, by_result, running, overdue, avg_length_days, license_switch_due: [{pilot_id, company_id, domain, switch_date}]}` |

Report: `running` = pending, started, not past `end_date`; `overdue` = pending past `end_date`;
`avg_length_days` = mean `end_date - start_date` of non-pending pilots with an end date (null when none);
`license_switch_due` = successful pilots whose `license_switch_date` falls within 30 days of `as_of`.

## Sender routes, verification and compliance (Q3, Q5, Q7)

| Method | Path | Body / params | Success |
|---|---|---|---|
| POST | `<channel_idx>/imports/` | multipart `file`, optional `sender_route` (`[a-z0-9_-]*`) | 202 `ImportBatchResponse` with `sender_route` |
| POST | `<channel_idx>/companies/bulk-sender-route/` | `company_ids` (1..1000), `sender_route` (empty clears) | 200 `{updated}`; Activity `note` per changed company |
| GET / PATCH | `<channel_idx>/settings/` | PATCH `default_sender_route` and/or `resume_days` (`{not_now?, out_of_office?}`, each 1..365; replaces the map) | 200 `{default_sender_route, resume_days, resume_defaults}` (`leads.settings` area) |
| GET | `<channel_idx>/companies/?sender_route=` | exact key; empty = without one | `CompanyResponse.sender_route` |
| POST | `<channel_idx>/contacts/<id>/verify/` | none | 200 `ContactResponse` (`email_status`, `email_status_reason`, `email_verified_at`); 400 without an address |
| GET | `<channel_idx>/contacts/?email_status=` | `unknown`, `valid`, `risky`, `invalid` | the visible list of bad addresses |
| GET | `<channel_idx>/reports/verification/` | none | 200 `{enabled, max_age_days, counts, stale}` |
| GET | `<channel_idx>/contacts/<id>/send-check/` | none | 200 `{eligible, reasons[], legal_basis{value, consent_ref, source, set_at}, email{status, reason, verified_at}, opt_out_at, company_do_not_contact, retention{days, exempt_won, anonymised_at, due_at}, sender_route}` |
| GET | `<channel_idx>/do-not-contact/` | none | 200 `{contacts[], companies[], suppressions[]}` (max 500 each) |
| POST | `<channel_idx>/do-not-contact/` | exactly one of `email`, `domain`; `reason` | 201 `{contacts, companies}` newly stopped; no delete endpoint by design |

## What to do today and the "not now" loop (Q16, Q20)

| Method | Path | Body / params | Success |
|---|---|---|---|
| GET | `<channel_idx>/queue/` | none (`leads.companies` area) | 200 `QueueResponse`: `{generated_at, counts{reply, task, hot_company, decision, drafts}, drafts{count, approvals_path}, items[]}` |

`items[]` is one list sorted by `value` (highest first), rows of `kind`: `reply` (communicator reply with `needs_review`,
`ref_id` = reply id, `excerpt`, `reason` = intent class or `unclassified`; value 100 interested, 80 unclear/unclassified,
60 wrong person, 55 not now, 50 out of office, 40 refusals), `drafts` (one summary row, value 70, `count`, `path` =
`/leads/approvals`), `task` (a contact whose "not now" day is today or overdue, value 65 + overdue days up to 10, `due_at`),
`hot_company` (latest score in `LEADS_QUEUE_SEGMENTS`, not disqualified, open stage, no draft or sent message yet; value
80/60 for S1/S2 + points/10 up to 9, `reason` = segment), `decision` (contact flagged `wrong_person` or, once Q9 lands,
`possibly_left` within `LEADS_QUEUE_REPLY_DAYS`; value 45). Every section is cut at `LEADS_QUEUE_LIMIT` rows; `counts`
are before the cut. `ContactResponse` gains `resume_at` and `resume_intent` (the planned return of a "not now").

## Rules and prompt profiles

| Method | Path | Body / params | Success |
|---|---|---|---|
| GET | `<channel_idx>/rules/` | — | 200 `{results: [RuleResponse]}` in evaluation order |
| POST | `<channel_idx>/rules/` | `trigger` (`stage_entered`, `intel_ready`; required), `stage_id` (required for `stage_entered`), `action` (`communicate` default, `request_audit`, `score`), `template_key` (required for `communicate`), `contact_strategy` (`primary`, `ai_pick`), `require_hooks` (true), `require_email` (true), `cooldown_hours` (24, 0–8760), `is_active`, `order` | 201 `RuleResponse` |
| GET / PATCH / DELETE | `<channel_idx>/rules/<id>/` | PATCH: any field of POST, null only for `stage_id` | 200 / 200 / 204 (runs deleted with the rule) |
| GET | `<channel_idx>/rule-runs/` | `company` (id), paging | 200 `RuleRunListResponse` — `id, rule_id, company_id, fired_at, outcome (fired|skipped|blocked|cooldown, empty while claimed), detail` |
| GET | `<channel_idx>/analysis-profiles/` | — | 200 `{results: [{id, key, model, is_active}]}` |
| POST | `<channel_idx>/analysis-profiles/` | `key` (default use: `leads.analysis`), `prompt_text`, `json_schema`, `model`, `is_active` | 201 profile with `prompt_text`, `json_schema` |
| GET / PATCH / DELETE | `<channel_idx>/analysis-profiles/<id>/` | PATCH: any field, no nulls | 200 / 200 / 204 |
| GET | `<channel_idx>/recipient-profiles/` | — | 200 `{results: [{id, key, model}]}` |
| POST | `<channel_idx>/recipient-profiles/` | `key` (default use: `leads.pick_recipient`), `prompt_text`, `json_schema`, `model` | 201 profile with `prompt_text`, `json_schema` |
| GET / PATCH / DELETE | `<channel_idx>/recipient-profiles/<id>/` | PATCH: any field, no nulls | 200 / 200 / 204 |

### Scoring

| Method | Path | Body / params | Success |
|---|---|---|---|
| GET | `<channel_idx>/reply-routes/` | — | 200 `{results: [ReplyRouteResponse]}`: one entry per reply intent class (`interested`, `not_now`, `not_interested`, `out_of_office`, `wrong_person`, `unsubscribe`, `unclear`) with `action` (`reply_stage` · `stage` · `none`), `stage_id`, `stage_key`, `flag_contact`, `is_default` |
| PUT / DELETE | `<channel_idx>/reply-routes/<intent>/` | PUT: `action` (default `reply_stage`), `stage_id` (needed for `stage`, forbidden otherwise, a stage of the channel), `flag_contact`; replaces the whole route | 200 / 200 (back to the default); 400 on a bad combination, 404 on an unknown intent |
| GET | `<channel_idx>/scoring-profiles/` | — | 200 `{results: [ScoringProfileResponse]}` |
| POST | `<channel_idx>/scoring-profiles/` | `key` (default `leads.scoring`), `thresholds` `{S, M, L}` (points, `S <= M <= L`), `rules` `[{field, op, value, points, label}]`, `disqualifiers` `[{field, op, value, label}]`, `is_active` | 201 `ScoringProfileResponse`; 409 `PROFILE_EXISTS` |
| GET / PATCH / DELETE | `<channel_idx>/scoring-profiles/<id>/` | PATCH: any field, no nulls | 200 / 200 / 204 |
| POST | `<channel_idx>/companies/<id>/score/` | — | 200 `{segment, points, disqualified, reasons, profile_id}`; Activity `score`; 409 `NO_SCORING_PROFILE` |

`field` is one of `lead_type, industry, platform, domain, hooks_count, emails_count, rotation_count`; `op` is `eq`,
`in` (list of strings), `contains` (text) or `gte`/`lte` (counts). Points: >= `L` is S1, >= `M` S2, >= `S` S3, else S4;
a matching disqualifier forces S4. A rule with `action: score` (`stage_entered` or `intel_ready`) scores the company and
writes the same Activity; without an active profile the run is `skipped`.

Lists never carry `prompt_text`. `require_legal_basis` exists on the model but is not exposed and has no effect.

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
| POST | `<channel_idx>/test/resume-now/` | `as_of` (aware datetime, optional), `company_id` (optional) | 200 `{resumed, cancelled, waiting}` — the "not now" resume of this channel in-process, whatever `LEADS_RESUME_ENABLED` says |
| POST | `<channel_idx>/test/digest-now/` | — | 200 `{sent: {channel_idx: 0 or 1}}` — the digest of this channel through notifications |
| POST | `<channel_idx>/test/retry-analyses/` | — | 200 `{recovered, failed}` — the intel analysis retry in-process, **every** channel |
| POST | `<channel_idx>/test/anonymise-now/` | `as_of` (aware datetime, optional) | 200 `{anonymised: {channel_idx: count}}` — the retention task in-process, **every** channel |

## Errors

Validation and DRF errors go through the host's v2 exception handler:
`{error, message, debug_id, details: [{field, location, issue, description}]}`.

| Status | Cause |
|---|---|
| 400 | schema validation (unknown field, bad enum, `sort` outside the allowlist, `consent` without `consent_ref`, null where not allowed); unknown `language`; missing, oversized or non-UTF-8 upload; `contact_id` not a contact with an email in the company; rule `clean()` (stage required / from another channel, template required) |
| 401 / 403 | no or invalid JWT / not staff |
| 404 | unknown channel, row of another channel, unknown `stage_key`, development endpoint outside development |
| 409 | one of the codes below |
| 503 | `communicate/` with an AI template while the toolbox is switched off (`AI_TOOLBOX_ENABLED = False`): body `{error: "AI_TOOLBOX_DISABLED", message}`, no draft, Activity `skipped: AI toolbox disabled` |

Every 409 uses the same v2 shape, `{error, message, debug_id, details}` — `error` is the code below,
upper-cased; `message` is human-readable; `details` is empty for these (no field-level errors).

| Code (`error`) | Raised by |
|---|---|
| `STAGE_EXISTS` | `POST`/`PATCH stages/`: another stage of the channel already has this key |
| `STAGE_NOT_EMPTY` | `DELETE stages/<pk>/`: the stage still holds companies |
| `LEAD_TYPE_EXISTS` | `POST lead-types/`: the channel already has this code, or the code is the built-in `UNKNOWN` |
| `LEAD_TYPE_IN_USE` | `DELETE lead-types/<pk>/`: companies of the channel still carry the code — retype them or deactivate the type |
| `DOMAIN_EXISTS` | `POST companies/`: another company of the channel already has this registrable domain |
| `NO_STAGES` | `POST companies/`: the channel has no pipeline yet |
| `CONTACT_EXISTS` | `POST contacts/`, `PATCH contacts/<id>/` (`email`): the company already has a contact with this email |
| `PROFILE_EXISTS` | `POST`/`PATCH {analysis,recipient,scoring}-profiles/`: another profile of the channel already has this key |
| `NO_SCORING_PROFILE` | `companies/<id>/score/`: the channel has no active scoring profile |
| `COMMUNICATOR_CHANNEL_MISSING` | `communicate/`: no communicator channel is configured |
| `NOT_ELIGIBLE` | `communicate/`: the outreach gate refused the contact (`do_not_contact`, `opted_out`, `anonymised`, `wrong_person`, `no_legal_basis`) |
| `NO_DRAFT` | `communicate/`: no draft could be built (no clause set) — see the company timeline |
| `NOT_IMPLEMENTED` | `create-customer/`: no Customer owns the primary contact's email — v1 never creates accounts |
