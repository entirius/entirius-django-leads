# AGENTS.md

entirius-django-leads (Django app `django_leads`) — leads and B2B pipeline module of Entirius: companies,
contacts, stages, rules, recipient selection, retention and GDPR. Table prefix `django_leads_`.

## Commands

| Command | Meaning |
|---|---|
| `make install` | `uv sync --all-extras` |
| `make test` | pytest — sqlite by default, PostgreSQL with `DATABASE_URL` |
| `make check` / `fix` | ruff lint + format (+ canonical `.gitleaks.toml` guard) |

## Conventions

- English only; MPL-2.0 header on every `.py` (`insert-license`); no Claude/AI attribution in commits or PRs.
- Layered: `models/` · `services/` (all logic, field whitelists) · `schemas/` · `api/admin/` (thin views) ·
  `signals/` · `tasks/`. No logic in models or views.
- Never rename the package, the app label or the table prefix; never edit a released migration.
- Access: areas live on the AppConfig (`access_areas`, `access_route_rules`), every admin view carries
  `access_area`; a new admin route without one fails `tests/test_access_ownership.py`.
- Git flow: `develop` + `master`, PRs, semver tag on `master`. Do not commit by default — the operator decides.

## Architecture

```
src/django_leads/
├── apps.py (connects receivers)  enums.py  settings.py (read at import)  urls.py  admin.py
├── models/       channel stage stage_event_rule company contact activity import_batch stage_rule rule_run claim
│                 analysis_profile recipient_pick_profile erased_address lead_type attribute_definition
│                 scoring_profile
├── schemas/      requests.py  responses.py
├── api/admin/    urls.py  views/ _base (auth, paging, Conflict)  company  company_action  contact  stage
│                 stage_event  activity  import  report  rule  profile  gdpr  attribute  scoring  channel
│                 customer_link (accounts only)
│                 test_views (development)
├── services/     activity (only Activity writer)  company  contact  stage (transition_stage)  import  sweep
│                 form  rule  recipient (outreach gate)  outreach (request_draft)  intel  rotation  claim
│                 retention  gdpr  erased_address  config  customer_link  alert  stage_event (the map)
│                 suppression (communicator do-not-contact)  mailable  funnel  attribute (values)
│                 fact (the one accessor)  condition  scoring  follow_up (tasks after a reply)
│                 task_bridge (the only django_todo importer)  channel (settings)
├── signals/      __init__ (stage_entered, contact_anonymised)  _deferred (after_commit)
│                 leads_receivers  siteintel_receivers  communicator_receivers  contact_forms_bridge
│                 todo_receivers (timeline, soft)
├── tasks/        import_tasks  intel_tasks  rule_tasks  rotation_tasks  retention_tasks   (queue leads_default)
├── gdpr/         hooks (leads' own)  protocol (GdprHooks)  registry (discover at call time)
├── connectors/   base (Connector, CandidateRow)  csv  twenty (interface only)  registry (entry points)
├── utils/        domains (registrable_domain)  emails (normalize, hash, token)  prompts  refs
└── management/commands/  leads_import_csv  leads_gdpr
```

Flow: import / form / manual → first stage → `transition_stage` → `stage_entered` → `rule_service` (claim under
lock → pick → `request_draft` = gate + communicator draft) → `message_sent` / `reply_received` /
`sequence_finished` → timeline, event → stage map, rotation → retention anonymises idle contacts.

| Topic | Where |
|---|---|
| A setting's name, default, meaning; Celery queue and beat | `docs/install.md` |
| Endpoint, params, response shape, error body | `docs/api.md`; `docs/openapi.yaml`; `schemas/` |
| Models, dedup, legal basis, rules phases, outreach gate | `docs/concept.md` |
| Which signals leads emits and consumes | `docs/concept.md` § Integration; `signals/` |
| CSV columns, skip reasons, sweep, GDPR, retention, monitoring | `docs/operations.md` |
| Which test file covers what; BDD and e2e in the entirius-zeno harness | `docs/testing.md` |
| ERD groupings | `docs/erd-config.yaml` |
| Why the module is shaped this way | `docs/concept.md` |
| Before editing imports, rules, the gate, receivers or GDPR | `docs/gotchas.md` |
| What changed and why | `CHANGELOG.md` |

## Gotchas

- `docs/gotchas.md` is the only list. Read it before touching imports, rules, the gate, receivers or GDPR.
