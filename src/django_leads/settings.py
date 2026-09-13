# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Module settings — host overrides via Django settings of the same name."""

from django.conf import settings

QUEUE_DEFAULT = getattr(settings, "LEADS_QUEUE_DEFAULT", "leads_default")

# CSV import: rows per chunk — counts and the report are saved after every chunk.
LEADS_IMPORT_CHUNK_SIZE = getattr(settings, "LEADS_IMPORT_CHUNK_SIZE", 500)

# contact_forms bridge: body keys read from `Lead.raw_data`.
LEADS_FORM_CONSENT_KEYS = getattr(settings, "LEADS_FORM_CONSENT_KEYS", ["marketing_consent"])
LEADS_FORM_WEBSITE_KEYS = getattr(settings, "LEADS_FORM_WEBSITE_KEYS", ["website", "url"])

# An address on these hosts says nothing about the company — never used as a company domain.
LEADS_FREEMAIL_DOMAINS = getattr(
    settings, "LEADS_FREEMAIL_DOMAINS", ["gmail.com", "wp.pl", "o2.pl", "onet.pl", "interia.pl", "outlook.com"]
)
