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
    FORM_IMPORT_FAILED = "form_import_failed", "Form import failed"
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
    ROTATION_FAILED = "rotation_failed", "Rotation failed"
    ROTATION_GAVE_UP = "rotation_gave_up", "Rotation gave up"
    LEGAL_BASIS = "legal_basis", "Legal basis"
    LEGAL_BASIS_CONFLICT = "legal_basis_conflict", "Legal basis conflict"
    INTEL = "intel", "Intel"
    INTEL_EMPTY = "intel_empty", "Intel empty"


class ImportStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"


class RuleTrigger(models.TextChoices):
    STAGE_ENTERED = "stage_entered", "Stage entered"
    INTEL_READY = "intel_ready", "Intel ready"


class RuleAction(models.TextChoices):
    REQUEST_AUDIT = "request_audit", "Request audit"
    COMMUNICATE = "communicate", "Communicate"


class ContactStrategy(models.TextChoices):
    PRIMARY = "primary", "Primary contact"
    AI_PICK = "ai_pick", "AI pick"


class RuleOutcome(models.TextChoices):
    FIRED = "fired", "Fired"
    SKIPPED = "skipped", "Skipped"
    BLOCKED = "blocked", "Blocked"
    COOLDOWN = "cooldown", "Cooldown"


class ClaimState(models.TextChoices):
    """Lifecycle of a claim around a paid call: `retry` waits for the next rotation or intel retry, `failed` is final."""

    CLAIMED = "claimed", "Claimed"
    DONE = "done", "Done"
    RETRY = "retry", "Retry"
    FAILED = "failed", "Failed"
