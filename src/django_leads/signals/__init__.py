# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Signals of django_leads."""

from django.dispatch import Signal

# Sent by `stage_service.transition_stage` on a real stage change. Args: company, stage.
stage_entered = Signal()
# Sent on commit when a contact is anonymised. Args: email_hash (sha256 of the normalised old address),
# anonymised_email (the token now stored), subject_ref ("leads.Company:<id>") — never the original email.
contact_anonymised = Signal()
# Sent on commit by `deal_service.transition` on a real stage change. Args: deal, previous (DealStage), stage.
deal_stage_changed = Signal()
# Sent on commit when a company has no contact left that may be mailed, because its contact turned out to be the
# wrong person or opted out — someone may look for another person (django_prospecting does). Args: subject_ref
# ("leads.Company:<id>"), channel_idx, company_name, domain, reason ("wrong_person" | "opted_out").
contact_needed = Signal()
