# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models


class StageKind(models.TextChoices):
    OPEN = "open", "Open"
    WON = "won", "Won"
    LOST = "lost", "Lost"
    UNRESPONSIVE = "unresponsive", "Unresponsive"


class CompanyType(models.TextChoices):
    MANUFACTURER = "MANUFACTURER", "Manufacturer"
    WHOLESALE = "WHOLESALE", "Wholesale"
    RETAILER = "RETAILER", "Retailer"
    UNKNOWN = "UNKNOWN", "Unknown"


class LeadSource(models.TextChoices):
    CSV = "csv", "CSV import"
    FORM = "form", "Contact form"
    MANUAL = "manual", "Manual"
    CONNECTOR = "connector", "Connector"


class ActivityKind(models.TextChoices):
    IMPORT = "import", "Import"
    FORM = "form", "Form"
    STAGE = "stage", "Stage"
    RULE = "rule", "Rule"
    DRAFT = "draft", "Draft"
    SENT = "sent", "Sent"
    REPLY = "reply", "Reply"
    BOUNCE = "bounce", "Bounce"
    OPTOUT = "optout", "Opt-out"
    NOTE = "note", "Note"
    ANONYMISED = "anonymised", "Anonymised"
    BLOCKED = "blocked", "Blocked"
    SKIPPED = "skipped", "Skipped"
    ROTATION = "rotation", "Rotation"


class ImportStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"
