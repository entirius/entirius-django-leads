# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Module settings — host overrides via Django settings of the same name."""

import tempfile
from pathlib import Path

from django.conf import settings

QUEUE_DEFAULT = getattr(settings, "LEADS_QUEUE_DEFAULT", "leads_default")

# CSV import: rows per chunk — counts and the report are saved after every chunk.
LEADS_IMPORT_CHUNK_SIZE = getattr(settings, "LEADS_IMPORT_CHUNK_SIZE", 500)
# Uploads wait here as `<batch_id>.csv` (0600, directory 0700) until their run ends; the service writes, the worker
# reads — both processes MUST see the same directory (a shared volume when they run in separate containers).
LEADS_IMPORT_TMP_DIR = getattr(settings, "LEADS_IMPORT_TMP_DIR", str(Path(tempfile.gettempdir()) / "django_leads"))
# A batch `pending`/`running` without progress for this long is failed (`stale`) by the sweep.
LEADS_IMPORT_STALE_MINUTES = getattr(settings, "LEADS_IMPORT_STALE_MINUTES", 30)
# Skipped rows kept in a batch report (row number + reason code); the counters always cover every row.
LEADS_IMPORT_REPORT_MAX = getattr(settings, "LEADS_IMPORT_REPORT_MAX", 1000)

# contact_forms bridge: body keys read from `Lead.raw_data`.
LEADS_FORM_CONSENT_KEYS = getattr(settings, "LEADS_FORM_CONSENT_KEYS", ["marketing_consent"])
LEADS_FORM_WEBSITE_KEYS = getattr(settings, "LEADS_FORM_WEBSITE_KEYS", ["website", "url"])

# An address on these hosts says nothing about the company — never used as a company domain.
LEADS_FREEMAIL_DOMAINS = getattr(
    settings, "LEADS_FREEMAIL_DOMAINS", ["gmail.com", "wp.pl", "o2.pl", "onet.pl", "interia.pl", "outlook.com"]
)

# Rotation: next contacts tried after a sequence ends without a reply, then the company parks as `unresponsive`.
LEADS_ROTATION_MAX = getattr(settings, "LEADS_ROTATION_MAX", 2)
LEADS_ROTATION_RETRY_HOURS = getattr(settings, "LEADS_ROTATION_RETRY_HOURS", 24)
LEADS_ROTATION_MAX_FAILURES = getattr(settings, "LEADS_ROTATION_MAX_FAILURES", 3)
LEADS_CLAIM_STALE_MINUTES = getattr(settings, "LEADS_CLAIM_STALE_MINUTES", 30)
# notifications recipient role of replies and failed analyses (soft dependency).
LEADS_NOTIFY_ROLE = getattr(settings, "LEADS_NOTIFY_ROLE", "sales_admin")
# Hooks kept from one intel analysis.
LEADS_ANALYSIS_MAX_HOOKS = getattr(settings, "LEADS_ANALYSIS_MAX_HOOKS", 10)

# Retention: contacts of companies inactive this long are anonymised (fallback when `Channel.retention_days` is null).
LEADS_RETENTION_DAYS = getattr(settings, "LEADS_RETENTION_DAYS", 180)
# Host of the tokens replacing anonymised addresses; communicator and agreements read the same setting.
LEADS_ANONYMISED_DOMAIN = getattr(settings, "LEADS_ANONYMISED_DOMAIN", "anonymised.invalid")
