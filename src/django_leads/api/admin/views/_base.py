# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Shared wiring of the admin views — auth declared explicitly, never inherited from service defaults."""

from collections.abc import Callable
from typing import TypeVar

from django.db.models import QuerySet
from django_utils.api.v2_errors import raise_pydantic_as_drf
from drf_spectacular.utils import OpenApiParameter
from pydantic import BaseModel, ValidationError
from rest_framework.exceptions import APIException, NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from django_leads.models import Channel

SchemaT = TypeVar("SchemaT", bound=BaseModel)

ERROR_RESPONSES = {400: None, 401: None, 403: None, 404: None}
PAGE_PARAMETERS = [OpenApiParameter("page", int), OpenApiParameter("page_size", int)]


class Conflict(APIException):
    status_code = 409
    default_detail = "The request conflicts with the current state."
    default_code = "conflict"


class AdminPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class AdminView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAdminUser]

    @staticmethod
    def channel(channel_idx: str) -> Channel:
        channel = Channel.objects.filter(idx=channel_idx).first()
        if channel is None:
            raise NotFound("Channel not found.")
        return channel

    @staticmethod
    def get_in(queryset: QuerySet, pk: int, label: str):
        """One row of a channel-scoped queryset; 404 when it belongs to another channel."""
        row = queryset.filter(pk=pk).first()
        if row is None:
            raise NotFound(f"{label} not found.")
        return row

    def paginated(self, request: Request, rows: QuerySet, dump: Callable[[object], BaseModel]) -> Response:
        paginator = AdminPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response([dump(row).model_dump(mode="json") for row in page])


def parse(schema: type[SchemaT], data: object) -> SchemaT:
    """Validate request data; a Pydantic error becomes the v2 400 shape."""
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise_pydantic_as_drf(exc)
