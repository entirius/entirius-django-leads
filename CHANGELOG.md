# Changelog

## 0.1.0 (unreleased)

- Scaffold from the Entirius module template.
- CRM core: channels, ordered stages, companies deduplicated by registrable domain, contacts by email,
  activity timeline, `transition_stage` + `stage_entered` signal.
- Chunked CSV import (command + admin API + Celery task) with an incremental report.
- contact_forms bridge: new form Leads become contacts with their legal basis.
- Admin API v2: companies, contacts, stages, activities, imports.
