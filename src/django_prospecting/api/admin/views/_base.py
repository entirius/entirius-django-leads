# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Shared wiring of the admin views — auth declared explicitly, never inherited from service defaults."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal, TypeVar

from django_utils.api.v2_errors import raise_pydantic_as_drf
from pydantic import BaseModel, ValidationError
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAdminUser
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from django_prospecting.apps import COMPANIES_AREA, PEOPLE_AREA
from django_prospecting.services import candidate_service, leads_bridge

SchemaT = TypeVar("SchemaT", bound=BaseModel)

ERROR_RESPONSES = {400: None, 401: None, 403: None, 404: None}
TAGS = ["Prospecting"]


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The candidate was already decided."
    default_code = "conflict"


class Unavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Leads is not available to take the contact."
    default_code = "leads_unavailable"


class PeopleView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAdminUser]
    access_area = PEOPLE_AREA


class CompaniesView(PeopleView):
    access_area = COMPANIES_AREA


def parse(schema: type[SchemaT], data: object, location: Literal["body", "query"] = "body") -> SchemaT:
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise_pydantic_as_drf(exc, location)


@contextmanager
def decision_errors(field: str) -> Iterator[None]:
    try:
        yield
    except candidate_service.CandidateStateError as exc:
        raise Conflict(str(exc)) from None
    except leads_bridge.LeadsUnavailable as exc:
        raise Unavailable(str(exc)) from None
    except ValueError as exc:
        raise DRFValidationError({field: [str(exc)]}) from None
