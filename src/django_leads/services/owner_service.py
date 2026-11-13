# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The owner of a company: who may own one (active staff people), the only writer of `Company.owner`, the list
filter. The owner is a relation, never a field of the generic company whitelist."""

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from django_leads.enums import ActivityKind
from django_leads.models import Activity, Channel, Company
from django_leads.services import activity_service

OWNER = "owner"
PICKER_LIMIT = 50
ASSIGN_LIMIT = 200
FILTER_ME = "me"
FILTER_NONE = "none"
# The reverse one-to-one of `django_access.Application.service_user`: the account an application's tokens act as.
ACCESS_SERVICE_USER = "access_application"


class UnknownOwner(Exception):
    """The id is not an active staff person."""


def _people() -> QuerySet:
    """Active staff, without the service users of access applications (when access is installed)."""
    users = get_user_model().objects.filter(is_active=True, is_staff=True)
    if apps.is_installed("django_access"):
        users = users.filter(**{f"{ACCESS_SERVICE_USER}__isnull": True})
    return users


def assignable_users(search: str = "") -> QuerySet:
    """Active staff people by name, at most `PICKER_LIMIT`."""
    users = _people()
    if search:
        match = Q(username__icontains=search) | Q(first_name__icontains=search) | Q(last_name__icontains=search)
        users = users.filter(match)
    return users.order_by("first_name", "last_name", "username")[:PICKER_LIMIT]


def resolve_owner(owner_id: int | None) -> AbstractBaseUser | None:
    """None clears the owner; any id outside the assignable users raises `UnknownOwner`."""
    if owner_id is None:
        return None
    owner = _people().filter(pk=owner_id).first()
    if owner is None:
        raise UnknownOwner(f"user {owner_id} cannot own companies")
    return owner


def set_owner(company: Company, owner: AbstractBaseUser | None, *, actor: str) -> bool:
    """Writes the owner and the Activity `owner: <username | none>`; the current owner changes nothing."""
    owner_id = owner.pk if owner else None
    if company.owner_id == owner_id:
        return False
    company.owner = owner
    company.save(update_fields=[OWNER, "modified_at"])
    activity_service.record(company, ActivityKind.NOTE, _note(owner), actor=actor)
    return True


def assign(channel: Channel, company_ids: list[int], owner: AbstractBaseUser | None, *, actor: str) -> int:
    """Bulk assign (≤ `ASSIGN_LIMIT` ids, every one a company of the channel, else `ValueError`); returns how many
    companies changed owner. One update and one Activity insert for the batch."""
    if len(company_ids) > ASSIGN_LIMIT:
        raise ValueError(f"at most {ASSIGN_LIMIT} companies at once")
    with transaction.atomic():
        companies = list(Company.objects.select_for_update().filter(channel=channel, pk__in=company_ids))
        missing = set(company_ids) - {company.pk for company in companies}
        if missing:
            raise ValueError(f"companies not in this channel: {sorted(missing)}")
        owner_id = owner.pk if owner else None
        changed = [company for company in companies if company.owner_id != owner_id]
        Company.objects.filter(pk__in=[c.pk for c in changed]).update(owner=owner, modified_at=timezone.now())
        note = _note(owner)
        activity_service.record_many(
            [Activity(company=c, kind=ActivityKind.NOTE, message=note, actor=actor) for c in changed]
        )
    return len(changed)


def adopt(company: Company, owner_id: int | None) -> bool:
    """Import: an unowned company takes the batch owner (in memory — the caller saves); True when it did."""
    if owner_id is None or company.owner_id is not None:
        return False
    company.owner_id = owner_id
    return True


def filter_by_owner(companies: QuerySet[Company], value: str, user_id: int | None) -> QuerySet[Company]:
    """`me` = the caller, `none` = unowned, digits = that user id, blank = any. Joins the owner for the response."""
    companies = companies.select_related(OWNER)
    if value == FILTER_ME:
        return companies.filter(owner_id=user_id)
    if value == FILTER_NONE:
        return companies.filter(owner__isnull=True)
    return companies.filter(owner_id=int(value)) if value else companies


def _note(owner: AbstractBaseUser | None) -> str:
    return f"owner: {owner.get_username() if owner else FILTER_NONE}"
