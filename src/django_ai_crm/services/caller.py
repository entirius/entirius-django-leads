# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Who calls the agent: the salesperson, the channel and the source — and what they may touch."""

from dataclasses import dataclass

from django.apps import apps
from django.contrib.auth.models import AbstractBaseUser

from django_ai_crm.enums import Source

TASKS_READ, TASKS_WRITE = "todo.tasks:read", "todo.tasks:write"
COMPANIES_READ, COMPANIES_WRITE = "leads.companies:read", "leads.companies:write"
REVIEW_READ, REVIEW_WRITE = "communicator.review:read", "communicator.review:write"
CONVERSATIONS_READ = "communicator.conversations:read"


class ToolError(Exception):
    """A refused tool call; `code` maps to the HTTP status of the REST API and to an MCP tool error."""

    STATUS = {"invalid": 400, "forbidden": 403, "not_found": 404, "conflict": 409, "unavailable": 503}

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)

    @property
    def status(self) -> int:
        return self.STATUS.get(self.code, 400)


@dataclass(frozen=True)
class Caller:
    user: AbstractBaseUser
    channel_idx: str
    source: str = Source.API

    @property
    def actor(self) -> str:
        """How every change is recorded: "<username> via ai-crm (<source>)"."""
        return f"{self.user.get_username()} via ai-crm ({self.source})"

    def can(self, permission: str) -> bool:
        """The caller's permission on an area of another module. With django_access the user's roles decide (an
        area its module does not declare = no); without it any active staff user may."""
        if not (self.user.is_active and self.user.is_staff):
            return False
        if not apps.is_installed("django_access"):
            return True
        from django_access.services import permissions

        try:
            return permissions.has_permission(self.user, permission)
        except ValueError:
            return False

    def require(self, permission: str) -> None:
        if not self.can(permission):
            raise ToolError("forbidden", f"You may not do this ({permission} is needed).")
