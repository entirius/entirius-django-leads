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
    TASK = "task", "Task"


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


class StageEvent(models.TextChoices):
    """Events of the event → stage map; `reply:<class>` falls back to `reply` (`stage_event_service`)."""

    FIRST_SENT = "first_sent", "First mail sent"
    REPLY = "reply", "Reply"
    REPLY_INTERESTED = "reply:interested", "Reply: interested"
    REPLY_NOT_NOW = "reply:not_now", "Reply: not now"
    REPLY_NOT_INTERESTED = "reply:not_interested", "Reply: not interested"
    REPLY_OUT_OF_OFFICE = "reply:out_of_office", "Reply: out of office"
    REPLY_WRONG_PERSON = "reply:wrong_person", "Reply: wrong person"
    REPLY_UNSUBSCRIBE = "reply:unsubscribe", "Reply: unsubscribe"
    REPLY_UNCLEAR = "reply:unclear", "Reply: unclear"
    SEQUENCE_FINISHED = "sequence_finished", "Sequence finished"


class ClaimState(models.TextChoices):
    """Lifecycle of a claim around a paid call: `retry` waits for the next rotation or intel retry, `failed` is final."""

    CLAIMED = "claimed", "Claimed"
    DONE = "done", "Done"
    RETRY = "retry", "Retry"
    FAILED = "failed", "Failed"


class AttributeEntity(models.TextChoices):
    """What a custom attribute describes; `deal` is accepted ahead of the deals model."""

    COMPANY = "company", "Company"
    CONTACT = "contact", "Contact"
    DEAL = "deal", "Deal"


class AttributeKind(models.TextChoices):
    TEXT = "text", "Text"
    NUMBER = "number", "Number"
    BOOL = "bool", "Yes / no"
    DATE = "date", "Date"
    SELECT = "select", "Select"
    MULTISELECT = "multiselect", "Multi-select"
    URL = "url", "URL"


class ConditionOp(models.TextChoices):
    """Operators of rule conditions and scoring rules; which apply to which kind is `condition_service.OPS_BY_KIND`."""

    EQ = "eq", "Equals"
    NE = "ne", "Not equal"
    IN = "in", "One of"
    CONTAINS = "contains", "Contains"
    GTE = "gte", "At least"
    LTE = "lte", "At most"
    EMPTY = "empty", "Empty"


class ScoreSegment(models.TextChoices):
    """S1 is the best segment, S4 the worst (and every disqualified company)."""

    S1 = "S1", "S1"
    S2 = "S2", "S2"
    S3 = "S3", "S3"
    S4 = "S4", "S4"
