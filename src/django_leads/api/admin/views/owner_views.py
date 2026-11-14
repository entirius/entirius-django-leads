# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — company owners: the user picker and bulk assignment."""

from django.contrib.auth.models import AbstractBaseUser
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, AdminView, parse
from django_leads.schemas.requests import AssignableUserQuery, OwnerAssignRequest
from django_leads.schemas.responses import OwnerAssignResponse, OwnerListResponse, OwnerResponse
from django_leads.services import owner_service

_TAGS = ["Leads Companies"]


def owner_or_400(owner_id: int | None) -> AbstractBaseUser | None:
    try:
        return owner_service.resolve_owner(owner_id)
    except owner_service.UnknownOwner as error:
        raise ValidationError({"owner_id": [str(error)]}) from None


class AssignableUserListView(AdminView):
    access_area = "leads.companies"

    @extend_schema(
        tags=_TAGS,
        operation_id="leads_assignable_users_list",
        summary="Users who can own a company",
        description="Active staff users by name, at most 50; id, username and name only.",
        parameters=[OpenApiParameter("search", str, description="Substring of username or name.")],
        responses={200: OwnerListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(AssignableUserQuery, request.query_params.dict())
        self.channel(channel_idx)
        users = owner_service.assignable_users(query.search)
        return Response(OwnerListResponse(results=[OwnerResponse.of(user) for user in users]).model_dump(mode="json"))


class OwnerAssignView(AdminView):
    access_area = "leads.companies"

    @extend_schema(
        tags=_TAGS,
        summary="Set or clear the owner of many companies",
        description="At most 200 companies, every one of the channel; each change is a timeline note.",
        request=OwnerAssignRequest,
        responses={200: OwnerAssignResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(OwnerAssignRequest, request.data)
        channel = self.channel(channel_idx)
        owner = owner_or_400(body.owner_id)
        try:
            updated = owner_service.assign(channel, body.company_ids, owner, actor=request.user.username)
        except ValueError as error:
            raise ValidationError({"company_ids": [str(error)]}) from None
        return Response(OwnerAssignResponse(updated=updated).model_dump(mode="json"))
