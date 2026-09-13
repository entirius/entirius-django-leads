# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Signals of django_leads."""

from django.dispatch import Signal

# Sent by `stage_service.transition_stage` on a real stage change. Args: company, stage.
stage_entered = Signal()
# Sent when a contact is anonymised (plan 11). Args: contact.
contact_anonymised = Signal()
