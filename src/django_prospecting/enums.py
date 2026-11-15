# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from django.db import models


class SearchReason(models.TextChoices):
    WRONG_PERSON = "wrong_person", "Wrong person"
    OPTED_OUT = "opted_out", "Opted out"
    MANUAL = "manual", "Manual"


class SearchStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"
    REFUSED = "refused", "Refused (limit)"


class CandidateStatus(models.TextChoices):
    PENDING = "pending", "Waiting for review"
    ACCEPTED = "accepted", "Accepted"
    REJECTED = "rejected", "Rejected"


class CheckKind(models.TextChoices):
    """The four questions about every person, in the order people read them."""

    ROLE = "role", "Role fits the offer"
    EMAIL = "email", "E-mail address known"
    EMPLOYED = "employed", "Still works there"
    MAILBOX = "mailbox", "Address works"


class CheckResult(models.TextChoices):
    OK = "ok", "OK"
    NO_INFO = "no_info", "No information"


class AddressVerdict(models.TextChoices):
    """What an address tester answers without sending a mail."""

    VALID = "valid", "Mailbox exists"
    INVALID = "invalid", "Mailbox does not exist"
    CATCH_ALL = "catch_all", "Domain accepts everything"
    UNKNOWN = "unknown", "Unknown"


class CompanySource(models.TextChoices):
    MANUAL = "manual", "Manual"
