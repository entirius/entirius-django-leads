# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from django.db import models


class Source(models.TextChoices):
    """Where a change came from; part of the actor every change is recorded with."""

    CMS = "cms", "CMS"
    API = "api", "API"
    MCP = "mcp", "MCP"


class ProposalStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    CONFIRMED = "confirmed", "Confirmed"
    REJECTED = "rejected", "Rejected"
    EXPIRED = "expired", "Expired"
    FAILED = "failed", "Failed"
