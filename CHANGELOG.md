# Changelog

## 0.1.0 (unreleased)

- Scaffold from the Entirius module template.
- CRM core: channels, ordered stages, companies deduplicated by registrable domain, contacts by email,
  activity timeline, `transition_stage` + `stage_entered` signal.
- Chunked CSV import (command + admin API + Celery task) with an incremental report.
- contact_forms bridge: new form Leads become contacts with their legal basis.
- Admin API v2: companies, contacts, stages, activities, imports.
- Stage rules (`stage_entered` / `intel_ready`) with a `RuleRun` per evaluation and a cooldown per (rule, company);
  drafts only through communicator `communicate()` (review required).
- siteintel intel analysis through the toolbox (`AnalysisProfile`): hooks, platform, company type.
- Recipient pick: primary contact or a validated toolbox pick (`RecipientPickProfile`).
- Rotation to the next contact after a finished sequence, `unresponsive` after `LEADS_ROTATION_MAX`.
- communicator receivers: reply → `on_reply` stage + notification, reviewer skip → `do_not_contact`, sent → timeline.
- Admin API v2: rules, rule runs, analysis and recipient profiles, company communicate / request-audit /
  create-customer (with django_accounts only); development endpoints `test/evaluate/`, `test/rotate-now/`.
