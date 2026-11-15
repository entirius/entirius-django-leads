# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from django.conf import settings
from django.db import models

from django_ai_crm.enums import ProposalStatus, Source


class Proposal(models.Model):
    """An irreversible action the agent asked to confirm (send a draft, lose a deal, do not contact).

    Only the user it was made for confirms or rejects it; confirming runs the tool again with the stored arguments.
    """

    channel_idx = models.CharField(max_length=64, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    tool = models.CharField(max_length=64)
    arguments = models.JSONField(default=dict)
    summary = models.CharField(max_length=255)
    source = models.CharField(max_length=8, choices=Source.choices)
    status = models.CharField(max_length=16, choices=ProposalStatus.choices, default=ProposalStatus.PENDING)
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["user", "channel_idx", "status"], name="ai_crm_proposal_user_status")]

    def __str__(self) -> str:
        return f"{self.tool} #{self.pk} ({self.status})"
