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

# A company created by a CSV import enters its first stage with the `stage_entered` signal, so the rules of that stage
# run (a site audit, a draft for review). False = imported companies wait until someone moves them.
LEADS_IMPORT_FIRES_RULES = getattr(settings, "LEADS_IMPORT_FIRES_RULES", True)

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
# Intel analyses failed transiently (toolbox down, timeout, 5xx) are retried by beat at most this many times.
LEADS_INTEL_RETRY_LIMIT = getattr(settings, "LEADS_INTEL_RETRY_LIMIT", 3)
# notifications recipient role of replies and failed analyses (soft dependency).
LEADS_NOTIFY_ROLE = getattr(settings, "LEADS_NOTIFY_ROLE", "sales_admin")
# Hooks kept from one intel analysis.
LEADS_ANALYSIS_MAX_HOOKS = getattr(settings, "LEADS_ANALYSIS_MAX_HOOKS", 10)

# Retention: contacts of companies inactive this long are anonymised (fallback when `Channel.retention_days` is null).
LEADS_RETENTION_DAYS = getattr(settings, "LEADS_RETENTION_DAYS", 180)
# Host of the tokens replacing anonymised addresses; communicator and agreements read the same setting.
LEADS_ANONYMISED_DOMAIN = getattr(settings, "LEADS_ANONYMISED_DOMAIN", "anonymised.invalid")

# Address verification (Q5). Off by default: nothing is checked live and no DNS query leaves the process; a stored
# `invalid` status still blocks outreach. On = the outreach gate verifies an unverified or stale address first.
LEADS_VERIFY_ENABLED = getattr(settings, "LEADS_VERIFY_ENABLED", False)
# A verified address older than this is checked again (gate and the daily task).
LEADS_VERIFY_MAX_AGE_DAYS = getattr(settings, "LEADS_VERIFY_MAX_AGE_DAYS", 30)
LEADS_VERIFY_BATCH = getattr(settings, "LEADS_VERIFY_BATCH", 200)
LEADS_VERIFY_DNS_TIMEOUT_S = getattr(settings, "LEADS_VERIFY_DNS_TIMEOUT_S", 5)
LEADS_DISPOSABLE_DOMAINS = getattr(
    settings,
    "LEADS_DISPOSABLE_DOMAINS",
    ["mailinator.com", "guerrillamail.com", "10minutemail.com", "yopmail.com", "tempmail.com", "trashmail.com",
     "sharklasers.com", "getnada.com", "dispostable.com", "throwawaymail.com"],
)  # fmt: skip
# SMTP RCPT probe: off by default (many hosts block it, and it touches third-party servers). Needs a sender address.
LEADS_VERIFY_SMTP_PROBE = getattr(settings, "LEADS_VERIFY_SMTP_PROBE", False)
LEADS_VERIFY_SMTP_FROM = getattr(settings, "LEADS_VERIFY_SMTP_FROM", "")
LEADS_VERIFY_SMTP_PER_MINUTE = getattr(settings, "LEADS_VERIFY_SMTP_PER_MINUTE", 10)
LEADS_VERIFY_SMTP_TIMEOUT_S = getattr(settings, "LEADS_VERIFY_SMTP_TIMEOUT_S", 10)
# Hook for an external verification API: dotted path of `callable(email) -> (status, reason) | None`; None = no opinion.
LEADS_VERIFY_EXTERNAL = getattr(settings, "LEADS_VERIFY_EXTERNAL", "")
# Hard bounces of communicator are read back this many days (the daily task); a missed signal is caught here.
LEADS_BOUNCE_SYNC_DAYS = getattr(settings, "LEADS_BOUNCE_SYNC_DAYS", 14)

# "Not now" loop (Q20). Off by default: a reply of class not_now / out_of_office is only noted and shown in the queue;
# on = the daily task drafts again to the contact on the due day, through the same outreach gate as every draft.
LEADS_RESUME_ENABLED = getattr(settings, "LEADS_RESUME_ENABLED", False)
# Days until a contact is approached again when the reply named no date; a channel overrides it in `resume_days`.
LEADS_RESUME_DEFAULT_DAYS = getattr(settings, "LEADS_RESUME_DEFAULT_DAYS", {"not_now": 30, "out_of_office": 7})
# Upper bound of drafts one run requests (the limit of the loop); the rest waits for the next run.
LEADS_RESUME_BATCH = getattr(settings, "LEADS_RESUME_BATCH", 50)

# "What to do today" queue (Q16): segments that count as over the scoring threshold, rows per section, and the age
# after which an unhandled reply drops out of the queue.
LEADS_QUEUE_SEGMENTS = getattr(settings, "LEADS_QUEUE_SEGMENTS", ["S1", "S2"])
LEADS_QUEUE_LIMIT = getattr(settings, "LEADS_QUEUE_LIMIT", 50)
LEADS_QUEUE_REPLY_DAYS = getattr(settings, "LEADS_QUEUE_REPLY_DAYS", 14)
# Daily digest (Q16): off by default; on = one notification per channel with what the automation did and refused.
LEADS_DIGEST_ENABLED = getattr(settings, "LEADS_DIGEST_ENABLED", False)
LEADS_DIGEST_HOURS = getattr(settings, "LEADS_DIGEST_HOURS", 24)
