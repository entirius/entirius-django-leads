# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models


class StageKind(models.TextChoices):
    OPEN = "open", "Open"
    WON = "won", "Won"
    LOST = "lost", "Lost"
    UNRESPONSIVE = "unresponsive", "Unresponsive"


# The built-in lead type of a company nobody has typed yet; never a `LeadType` row, always accepted.
UNKNOWN_LEAD_TYPE = "UNKNOWN"


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
    SCORE = "score", "Score"


class ReplyIntent(models.TextChoices):
    """The intent classes of a reply (communicator T4); the same values, leads does not import them."""

    INTERESTED = "interested", "Interested"
    NOT_NOW = "not_now", "Not now"
    NOT_INTERESTED = "not_interested", "Not interested"
    OUT_OF_OFFICE = "out_of_office", "Out of office"
    WRONG_PERSON = "wrong_person", "Wrong person"
    UNSUBSCRIBE = "unsubscribe", "Unsubscribe"
    UNCLEAR = "unclear", "Unclear"


class ReplyStageAction(models.TextChoices):
    REPLY_STAGE = "reply_stage", "Move to the on-reply stage"
    STAGE = "stage", "Move to a stage"
    NONE = "none", "Keep the stage"


class PilotResult(models.TextChoices):
    PENDING = "pending", "Pending"
    SUCCESS = "success", "Success"
    FAILURE = "failure", "Failure"
    CANCELLED = "cancelled", "Cancelled"


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
    SCORE = "score", "Score"


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
