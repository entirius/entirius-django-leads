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
| GET | `<channel_idx>/companies/` | `stage` (key), `search` (substring of name or domain), `sort` (`name`, `domain`, `stage_entered_at`, `last_activity_at`, `-` prefix; default `name`), `company_type` (`MANUFACTURER`, `WHOLESALE`, `RETAILER`, `UNKNOWN`), `do_not_contact` (bool), `has_reply` (bool: at least one `reply` Activity), paging | 200 `CompanyListResponse` |
| POST | `<channel_idx>/companies/` | `domain` (required, domain or URL, stored registrable), `name` (default: the domain), `website`, `company_type`, `industry` | 201 `CompanyDetailResponse`; Activity `note` "company created" |
| GET | `<channel_idx>/companies/<id>/` | — | 200 `CompanyDetailResponse` |
| PATCH | `<channel_idx>/companies/<id>/` | any of `name`, `website` (null clears), `company_type`, `industry`, `description`, `do_not_contact`, `external_ref` | 200 `CompanyDetailResponse` |
| POST | `<channel_idx>/companies/<id>/transition/` | `stage_key` | 200 `CompanyDetailResponse`; Activity `stage`, `stage_entered` on commit (same stage: no-op) |
| POST | `<channel_idx>/companies/<id>/communicate/` | `template_key`, `contact_id` | 201 `{message_id, status}` — a reviewable communicator draft |
| POST | `<channel_idx>/companies/<id>/request-audit/` | — | 202 `{audit_id, status}`; Activity `intel` "audit requested" |
| POST | `<channel_idx>/companies/<id>/create-customer/` | — (routed only with `django_accounts`) | 200 `{customer_uid}` |

`CompanyResponse`: `id, name, domain, website, company_type, industry, description, platform, hooks, stage
{id, key, label, order, kind, is_terminal, on_reply}, stage_entered_at, source, external_ref, do_not_contact,
customer_uid, rotation_count, last_activity_at`. `CompanyDetailResponse` adds `contacts` (all) and `activities`
(last 20, newest first). `stage`, `domain`, `hooks` and `customer_uid` are not writable here.

`communicate/` skips rule conditions and cooldown, not the outreach gate (`concept.md` § Outreach gate).
`create-customer/` links the Customer owning the primary contact's email through a **verified** allauth
`EmailAddress`; v1 never creates an account.

## Contacts

| Method | Path | Body / params | Success |
|---|---|---|---|
| GET | `<channel_idx>/contacts/` | `company` (id), paging | 200 `ContactListResponse` |
| POST | `<channel_idx>/contacts/` | `company_id` (required), `email`, `first_name`, `last_name`, `job_title`, `phone`, `language` (ISO 639-1), `is_primary`, `legal_basis`, `consent_ref` | 201 `ContactResponse` |
| GET | `<channel_idx>/contacts/<id>/` | — | 200 `ContactResponse` |
| PATCH | `<channel_idx>/contacts/<id>/` | any of `first_name`, `last_name`, `job_title`, `phone`, `language` (null clears), `is_primary`, `legal_basis` (null clears), `consent_ref` | 200 `ContactResponse` |

`ContactResponse`: `id, company_id, email, first_name, last_name, job_title, phone, language, is_primary, source,
legal_basis, opt_out_at, anonymised_at`. `email` is immutable — it is not a PATCH field. `legal_basis` is one of
`consent`, `legitimate_interest`, `contract`; `consent` requires a non-blank `consent_ref` (≤ 255), recorded in the
`legal_basis` Activity as `admin:<user>:<consent_ref>`; other bases as `admin:<user>`.

## Stages

| Method | Path | Body | Success |
|---|---|---|---|
| GET | `<channel_idx>/stages/` | — | 200 `{results: [StageResponse]}` in `order` |
| POST | `<channel_idx>/stages/` | `key` (slug, required), `label` (required), `order` (0–32767), `kind` (`open`, `won`, `lost`, `unresponsive`), `is_terminal`, `on_reply` | 201 `StageResponse` |
| GET | `<channel_idx>/stages/<id>/` | — | 200 `StageResponse` |
| PATCH | `<channel_idx>/stages/<id>/` | any field of POST, no nulls | 200 `StageResponse` |
| DELETE | `<channel_idx>/stages/<id>/` | — | 204 |

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

## Rules and prompt profiles

| Method | Path | Body / params | Success |
|---|---|---|---|
| GET | `<channel_idx>/rules/` | — | 200 `{results: [RuleResponse]}` in evaluation order |
| POST | `<channel_idx>/rules/` | `trigger` (`stage_entered`, `intel_ready`; required), `stage_id` (required for `stage_entered`), `action` (`communicate` default, `request_audit`), `template_key` (required for `communicate`), `contact_strategy` (`primary`, `ai_pick`), `require_hooks` (true), `require_email` (true), `cooldown_hours` (24, 0–8760), `is_active`, `order` | 201 `RuleResponse` |
| GET / PATCH / DELETE | `<channel_idx>/rules/<id>/` | PATCH: any field of POST, null only for `stage_id` | 200 / 200 / 204 (runs deleted with the rule) |
| GET | `<channel_idx>/rule-runs/` | `company` (id), paging | 200 `RuleRunListResponse` — `id, rule_id, company_id, fired_at, outcome (fired|skipped|blocked|cooldown, empty while claimed), detail` |
| GET | `<channel_idx>/analysis-profiles/` | — | 200 `{results: [{id, key, model, is_active}]}` |
| POST | `<channel_idx>/analysis-profiles/` | `key` (default use: `leads.analysis`), `prompt_text`, `json_schema`, `model`, `is_active` | 201 profile with `prompt_text`, `json_schema` |
| GET / PATCH / DELETE | `<channel_idx>/analysis-profiles/<id>/` | PATCH: any field, no nulls | 200 / 200 / 204 |
| GET | `<channel_idx>/recipient-profiles/` | — | 200 `{results: [{id, key, model}]}` |
| POST | `<channel_idx>/recipient-profiles/` | `key` (default use: `leads.pick_recipient`), `prompt_text`, `json_schema`, `model` | 201 profile with `prompt_text`, `json_schema` |
| GET / PATCH / DELETE | `<channel_idx>/recipient-profiles/<id>/` | PATCH: any field, no nulls | 200 / 200 / 204 |

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

Every 409 uses the same v2 shape, `{error, message, debug_id, details}` — `error` is the code below,
upper-cased; `message` is human-readable; `details` is empty for these (no field-level errors).

| Code (`error`) | Raised by |
|---|---|
| `STAGE_EXISTS` | `POST`/`PATCH stages/`: another stage of the channel already has this key |
| `STAGE_NOT_EMPTY` | `DELETE stages/<pk>/`: the stage still holds companies |
| `DOMAIN_EXISTS` | `POST companies/`: another company of the channel already has this registrable domain |
| `NO_STAGES` | `POST companies/`: the channel has no pipeline yet |
| `CONTACT_EXISTS` | `POST contacts/`: the company already has a contact with this email |
| `PROFILE_EXISTS` | `POST`/`PATCH {analysis,recipient}-profiles/`: another profile of the channel already has this key |
| `COMMUNICATOR_CHANNEL_MISSING` | `communicate/`: no communicator channel is configured |
| `NOT_ELIGIBLE` | `communicate/`: the outreach gate refused the contact (`do_not_contact`, `opted_out`, `anonymised`, `no_legal_basis`) |
| `NO_DRAFT` | `communicate/`: no draft could be built (no clause set) — see the company timeline |
| `NOT_IMPLEMENTED` | `create-customer/`: no Customer owns the primary contact's email — v1 never creates accounts |
