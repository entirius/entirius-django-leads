# AGENTS.md

entirius-django-leads — leads and B2B pipeline for Volkanos: companies, contacts, stages, rules, recipient
selection, retention and GDPR. App label `django_leads`, table prefix `django_leads_`.

## Quick Reference

- Python ≥ 3.11, Django ≥ 4.2, DRF + simplejwt + drf-spectacular + Pydantic, Celery (+ celery-once), `uv`, ruff,
  hatchling, MPL-2.0.
- Read first: `docs/install.md` (host) · `docs/api.md` (caller) · `docs/concept.md` (why) ·
  `docs/operations.md` (day 2) · `docs/gotchas.md` (before editing). This file is the map; it explains nothing twice.

## Commands

| Command | Meaning |
|---|---|
| `make install` | `uv sync --all-extras` — needs the sibling clones `../entirius-django-{agreements,contact-forms,communicator,siteintel,notifications,utils}` while `[tool.uv.sources]` points at them |
| `make test` | pytest — sqlite by default, PostgreSQL with `DATABASE_URL` |
| `make check` / `fix` | ruff lint + format (+ canonical `.gitleaks.toml` guard) |
| in zeno: `make module-test MODULE=entirius-django-leads` | the same suite inside the service container, on PostgreSQL |

## Conventions

- English only; MPL-2.0 header on every `.py` (`insert-license`); no Claude/AI attribution in commits or PRs.
- Layered: `models/` · `services/` (all logic, field whitelists) · `schemas/` · `api/admin/` (thin views) ·
  `signals/` · `tasks/`. No logic in models or views.
- Never rename the package, the app label or the table prefix; never edit a released migration.
- Git flow: `develop` + `master`, PRs, semver tag on `master`. Do not commit by default — the operator decides.

## Map

```
src/django_leads/
├── apps.py (connects receivers)  enums.py  settings.py (read at import)  urls.py  admin.py
├── models/       channel stage company contact activity import_batch stage_rule rule_run claim
│                 analysis_profile recipient_pick_profile erased_address
├── schemas/      requests.py  responses.py
├── api/admin/    urls.py  views/ _base (auth, paging, Conflict)  company  company_action  contact  stage
│                 activity  import  rule  profile  gdpr  customer_link (accounts only)  test_views (development)
├── services/     activity (only Activity writer)  company  contact  stage (transition_stage)  import  sweep
│                 form  rule  recipient (outreach gate)  outreach (request_draft)  intel  rotation  claim
│                 retention  gdpr  erased_address  config  customer_link  alert
├── signals/      __init__ (stage_entered, contact_anonymised)  _deferred (after_commit)
│                 leads_receivers  siteintel_receivers  communicator_receivers  contact_forms_bridge
├── tasks/        import_tasks  intel_tasks  rule_tasks  rotation_tasks  retention_tasks   (queue leads_default)
├── gdpr/         hooks (leads' own)  protocol (GdprHooks)  registry (discover at call time)
├── connectors/   base (Connector, CandidateRow)  csv  twenty (interface only)  registry (entry points)
├── utils/        domains (registrable_domain)  emails (normalize, hash, token)  prompts  refs
└── management/commands/  leads_import_csv  leads_gdpr
```

Flow: import / form / manual → first stage → `transition_stage` → `stage_entered` → `rule_service` (claim under
lock → pick → `request_draft` = gate + communicator draft) → `message_sent` / `reply_received` /
`sequence_finished` → timeline, `on_reply` stage, rotation → retention anonymises idle contacts.

## Where things live

| Question | Answer |
|---|---|
| A setting's name, default, meaning; Celery queue and beat | `docs/install.md` |
| Endpoint, params, response shape, error body | `docs/api.md`; `docs/openapi.yaml`; `schemas/` |
| Models, dedup, legal basis, rules phases, outreach gate | `docs/concept.md` |
| Which signals leads emits and consumes | `docs/concept.md` § Integration; `signals/` |
| CSV columns, skip reasons, sweep, GDPR, retention, monitoring | `docs/operations.md` |
| Which test file covers what | `docs/testing.md` |
| ERD groupings | `docs/erd-config.yaml` |
| What changed and why | `CHANGELOG.md` |

## Testing

- `tests/settings.py`: `DATABASE_URL` wins (CI, zeno), else sqlite in memory; `ENVIRONMENT = "development"`.
- `ToolboxClient` and `communicate()` are patched — no test leaves the process.
- `tests/test_openapi.py` fails on any schema warning; run it after touching a view or schema.
- `docs/openapi.yaml` is exported from the zeno service (`manage.py spectacular`) and filtered to `/api/leads/` —
  regenerate it with the API.

## Testing end-to-end

- Covered edge cases (roadmap catalogue): unit L-01 … L-19; BDD L-01, L-04, L-06, L-07, L-08, L-09, L-10, L-11,
  L-12, L-14, L-16, L-17, L-19. L-02, L-03, L-05, L-13, L-15, L-18 are unit-only.
- BDD (emporium `features/leads/`): `make bdd TAGS=@leads`; reference funnel `make bdd TAGS=@funnel`.
- One-shot tags: `@leads-oneshot` — L-14 (rotation), L-16 (retention), L-17 (GDPR erase) and the whole
  `@funnel` feature. A re-run needs a fresh `make seed`.
- e2e: `make e2e-funnel` (Playwright over the CMS, emporium `e2e/cms/test_leads_funnel.py`), `make e2e-accept`
  (AI-tester).
- Guides: portal `guides/leads-end-to-end-testing.md` (entirius-docs: modes A/B/C, prerequisites, triage) and
  emporium `docs/e2e-leads-funnel.md`.

## Gotchas

`docs/gotchas.md` — the only list. Read it before touching imports, rules, the gate, receivers or GDPR.
