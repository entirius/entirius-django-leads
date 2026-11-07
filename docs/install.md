---
title: Install
description: What a host needs before the first import — apps, URLs, settings, Celery queue and beat, optional extras, bootstrap order.
---

Read this once before `migrate`. Day-2 work (imports, retention, GDPR, monitoring): `operations.md`.

## Prerequisites

| Requirement | Why | Verify |
|---|---|---|
| Python ≥ 3.11, Django ≥ 4.2, DRF + `rest_framework_simplejwt` + `drf_spectacular` | the admin API is `JWTAuthentication` + `IsAdminUser` with Pydantic schemas | `manage.py check` |
| Hard-dependency apps installed: `django_regional`, `django_agreements` (≥ 2.1.0), `django_siteintel`, `django_communicator` | FKs to `Language`, `LegalBasis` + clause sets, audits, drafts; `apps.py` connects receivers to siteintel and communicator signals in `ready()` | `manage.py migrate` |
| `entirius-django-utils` ≥ 2.0.0 with its toolbox client (`django_utils.toolbox`) and `AI_TOOLBOX_BASE_URL` / `AI_TOOLBOX_API_KEY` / `AI_TOOLBOX_CHANNEL` | intel analysis and the `ai_pick` recipient pick are toolbox completions; `settings.AI_TOOLBOX_CHANNEL` is read directly | a `request-audit` → `intel analysed` Activity |
| `SPECTACULAR_SETTINGS["OAS_VERSION"] = "3.1.0"` | Pydantic examples are JSON Schema 2020-12 | `manage.py spectacular --validate` |
| The v2 exception handler (`django_utils.api.v2_errors`) as `REST_FRAMEWORK["EXCEPTION_HANDLER"]` | error bodies of `api.md` § Errors | a 400 carries `debug_id` |
| A Celery worker consuming **`leads_default`** (or `LEADS_QUEUE_DEFAULT`) | every task of the module | `celery -A main worker -Q …,leads_default` |
| celery-once configured (`app.conf.ONCE`) | `django_leads.anonymise_inactive` is a `QueueOnce` task | the daily retention run does not raise |
| Service and worker share `LEADS_IMPORT_TMP_DIR` | the API writes the upload, the worker reads it — separate containers need one volume mounted at that path in both | a queued import ends `done`, not `failed` (`missing_file`) |

## INSTALLED_APPS and URLs

```python
INSTALLED_APPS = [
    # ...
    "rest_framework",
    "rest_framework_simplejwt",
    "drf_spectacular",
    "django_regional",
    "django_agreements",
    "django_siteintel",
    "django_notifications",  # optional, see Extras
    "django_communicator",
    "django_contact_forms",  # optional, see Extras
    "django_leads",
]
```

```python
# main/urls.py
urlpatterns.append(path("", include("django_leads.urls")))
```

`django_leads.urls` mounts `api/leads/v2/admin/gdpr/{export,erase}/` and
`api/leads/v2/admin/<channel_idx>/…` (`api.md`). Two groups are routed conditionally, at import of the URL module:

- `companies/<id>/create-customer/` — only when `django_accounts` is installed.
- `test/import-now/`, `test/evaluate/`, `test/rotate-now/`, `test/anonymise-now/` — only when
  `ENVIRONMENT == "development"`; the views also answer 404 outside it.

## Settings

Defaults: `django_leads/settings.py`. Every value is read **once, at import** of that module — see `gotchas.md`.

| Setting | Default | Meaning |
|---|---|---|
| `LEADS_QUEUE_DEFAULT` | `"leads_default"` | Celery queue of every task |
| `LEADS_IMPORT_FIRES_RULES` | `true` | a company created by a CSV import enters its first stage with `stage_entered`, so its rules run; false = imported companies wait |
| `LEADS_IMPORT_CHUNK_SIZE` | `500` | CSV rows per chunk; counters, report and resume point commit with each chunk |
| `LEADS_IMPORT_TMP_DIR` | `<system temp>/django_leads` | uploads wait here as `<batch_id>.csv` (file 0600, directory 0700) |
| `LEADS_IMPORT_STALE_MINUTES` | `30` | a `pending`/`running` batch without progress this long is failed `stale`; also the form-submission retry cutoff |
| `LEADS_IMPORT_REPORT_MAX` | `1000` | skipped rows kept in a batch report; counters always cover every row |
| `LEADS_FORM_CONSENT_KEYS` | `["marketing_consent"]` | `Lead.raw_data` keys read as marketing consent |
| `LEADS_FORM_WEBSITE_KEYS` | `["website", "url"]` | `Lead.raw_data` keys read as the company website |
| `LEADS_FREEMAIL_DOMAINS` | `gmail.com`, `wp.pl`, `o2.pl`, `onet.pl`, `interia.pl`, `outlook.com` | an address on these hosts never names a company |
| `LEADS_ROTATION_MAX` | `2` | rotations to the next contact before the company parks in the `unresponsive` stage |
| `LEADS_ROTATION_RETRY_HOURS` | `24` | wait before a rotation that produced no draft is tried again |
| `LEADS_ROTATION_MAX_FAILURES` | `3` | no-draft attempts before the rotation gives up |
| `LEADS_INTEL_RETRY_LIMIT` | `3` | beat retries of an intel analysis that failed transiently (toolbox down, timeout, 5xx) |
| `LEADS_CLAIM_STALE_MINUTES` | `30` | a `claimed` rule run or claim older than this is failed `outcome_unknown`, never retried |
| `LEADS_NOTIFY_ROLE` | `"sales_admin"` | notifications recipient role of replies and failed analyses |
| `LEADS_ANALYSIS_MAX_HOOKS` | `10` | hooks kept from one intel analysis |
| `LEADS_RETENTION_DAYS` | `180` | idle days before contacts are anonymised, when `Channel.retention_days` is null |
| `LEADS_ANONYMISED_DOMAIN` | `"anonymised.invalid"` | host of anonymised-address tokens; communicator and agreements read the same setting — keep one value |
| `LEADS_VERIFY_ENABLED` | `False` | the gate verifies unverified or stale addresses and the daily task re-checks them; off = nothing queries DNS (a stored `invalid` still blocks) |
| `LEADS_VERIFY_MAX_AGE_DAYS` | `30` | a verification older than this is repeated |
| `LEADS_VERIFY_BATCH` | `200` | addresses checked per scheduled run |
| `LEADS_VERIFY_DNS_TIMEOUT_S` | `5` | DNS lifetime per lookup |
| `LEADS_DISPOSABLE_DOMAINS` | ten well-known hosts | `invalid / disposable`, subdomains included; replace with a fuller list |
| `LEADS_VERIFY_SMTP_PROBE` | `False` | RCPT probe of the first MX; needs `LEADS_VERIFY_SMTP_FROM`; touches third-party servers |
| `LEADS_VERIFY_SMTP_FROM` | `""` | MAIL FROM of the probe |
| `LEADS_VERIFY_SMTP_PER_MINUTE` | `10` | probe rate limit (shared cache) |
| `LEADS_VERIFY_SMTP_TIMEOUT_S` | `10` | probe socket timeout |
| `LEADS_VERIFY_EXTERNAL` | `""` | dotted path of `callable(email) -> (status, reason) or None`: hook for an external verification API (never overrides `invalid`) |
| `LEADS_BOUNCE_SYNC_DAYS` | `14` | hard bounces of communicator read back this far by the daily task |
| `LEADS_RESUME_ENABLED` | `False` | "not now" loop: the daily task drafts again to contacts whose day has come (always through the outreach gate); off = the day is only planned and shown in the queue |
| `LEADS_RESUME_DEFAULT_DAYS` | `{"not_now": 30, "out_of_office": 7}` | days until the return when the reply named no date; a channel overrides it in `resume_days` (settings API) |
| `LEADS_RESUME_BATCH` | `50` | drafts one resume run requests at most |
| `LEADS_QUEUE_SEGMENTS` | `["S1", "S2"]` | score segments that count as over the threshold in the queue |
| `LEADS_QUEUE_LIMIT` | `50` | rows per queue section |
| `LEADS_QUEUE_REPLY_DAYS` | `14` | age after which an unhandled reply or flagged contact leaves the queue |
| `LEADS_DIGEST_ENABLED` | `False` | the daily digest notification per channel; off = the task does nothing |
| `LEADS_DIGEST_HOURS` | `24` | window the digest covers |
| `ENVIRONMENT` | — | `"development"` routes and enables the `test/` endpoints |

## Celery

Every task runs on `LEADS_QUEUE_DEFAULT`. The host owns the beat schedule — the module registers none.

| Task | Trigger | Schedule the host adds |
|---|---|---|
| `django_leads.import_csv` | `POST imports/`, `leads_import_csv` without `--sync` | — |
| `django_leads.import_form_lead` | contact_forms bridge after an `OperationalError` | — |
| `django_leads.evaluate_rules` | `stage_entered` receiver | — |
| `django_leads.analyse_intel` | siteintel `report_ready` receiver | — |
| `django_leads.rotate_thread` | communicator `sequence_finished` receiver | — |
| `django_leads.fail_stale_import_batches` | beat | every 10 minutes |
| `django_leads.retry_failed_analyses` | beat | every 10 minutes |
| `django_leads.rotate_unresponsive` | beat | daily |
| `django_leads.anonymise_inactive` | beat | daily |
| `django_leads.reverify_addresses` | beat | daily (hard bounces always; address checks only with `LEADS_VERIFY_ENABLED`) |
| `django_leads.resume_contacts` | beat | daily; does nothing until `LEADS_RESUME_ENABLED` |
| `django_leads.send_digest` | beat | daily, in the morning; does nothing until `LEADS_DIGEST_ENABLED` |

```python
CELERY_BEAT_SCHEDULE = {
    "leads-fail-stale-imports": {"task": "django_leads.fail_stale_import_batches", "schedule": 600},
    "leads-retry-analyses": {"task": "django_leads.retry_failed_analyses", "schedule": 600},
    "leads-rotate-unresponsive": {"task": "django_leads.rotate_unresponsive", "schedule": crontab(hour=5, minute=0)},
    "leads-anonymise-inactive": {"task": "django_leads.anonymise_inactive", "schedule": crontab(hour=4, minute=0)},
    "leads-reverify-addresses": {"task": "django_leads.reverify_addresses", "schedule": crontab(hour=3, minute=0)},
    "leads-resume-contacts": {"task": "django_leads.resume_contacts", "schedule": crontab(hour=6, minute=0)},
    "leads-send-digest": {"task": "django_leads.send_digest", "schedule": crontab(hour=7, minute=0)},
}
```

## Extras

| Extra | Installs | Without it |
|---|---|---|
| `contact-forms` | `entirius-django-contact-forms` | no form bridge; the sweep skips form retries |
| `notifications` | `entirius-django-notifications` | replies and failed analyses are logged only (one warning per process) |
| — (`django_accounts` in the host) | — | `companies/<id>/create-customer/` is not routed and its module never imported |

Connectors are entry points in the group `leads_connectors`; the package registers `csv` and `twenty` (interface
only). Another package adds its own by declaring an entry point in that group.

## Bootstrap order

```
manage.py migrate                        # 0001–0008
# Django admin: create a Channel (idx = the channel of communicator, agreements and siteintel) with its stages
#   — the lowest `order` is where new companies enter; one stage with kind=unresponsive for rotation;
#   one stage with on_reply=True to move replying companies; one with on_sent=True (usually "contacted") for the
#   first sent message
# admin API: analysis-profiles/ (key leads.analysis), recipient-profiles/ (key leads.pick_recipient), rules/
manage.py leads_import_csv --channel <idx> --file leads.csv --sync   # first import, no worker needed
```

A channel without stages fails every import (`no_stages`) and every manual company create (409). Drafts need the
communicator channel, templates and an agreements clause set per legal basis and language of the same `idx`.
