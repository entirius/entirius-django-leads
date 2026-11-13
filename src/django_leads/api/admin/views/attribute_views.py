# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — custom attribute definitions (CRUD) and the fact catalogue (`fields/`)."""

from collections.abc import Iterator
from contextlib import contextmanager

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, AdminView, Conflict, parse
from django_leads.models import AttributeDefinition
from django_leads.schemas.requests import (
    AttributeDefinitionRequest,
    AttributeDefinitionUpdateRequest,
    AttributeListQuery,
)
from django_leads.schemas.responses import (
    AttributeDefinitionListResponse,
    AttributeDefinitionResponse,
    FieldListResponse,
)
from django_leads.services import attribute_service, fact_service

_TAGS = ["Leads Attributes"]


def dump(definition: AttributeDefinition) -> dict:
    return AttributeDefinitionResponse.model_validate(definition).model_dump(mode="json")


@contextmanager
def service_errors() -> Iterator[None]:
    try:
        yield
    except attribute_service.DefinitionExists as error:
        raise Conflict(str(error), code="attribute_exists") from None
    except attribute_service.DefinitionInUse as error:
        raise Conflict(str(error), code="attribute_in_use") from None
    except ValueError as error:
        raise ValidationError({"detail": [str(error)]}) from None


class AttributeListView(AdminView):
    access_area = "leads.settings"

    @extend_schema(
        tags=_TAGS,
        operation_id="leads_attributes_list",
        summary="Attribute definitions of the channel",
        parameters=[OpenApiParameter("entity", str, description="company, contact or deal.")],
        responses={200: AttributeDefinitionListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(AttributeListQuery, request.query_params.dict())
        definitions = attribute_service.list_definitions(self.channel(channel_idx), query.entity or "")
        return Response({"results": [dump(definition) for definition in definitions]})

    @extend_schema(
        tags=_TAGS,
        summary="Define an attribute",
        description="The key may not be a built-in fact name; options are required for select and multiselect.",
        request=AttributeDefinitionRequest,
        responses={201: AttributeDefinitionResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        fields = parse(AttributeDefinitionRequest, request.data).model_dump(mode="json")
        with service_errors():
            definition = attribute_service.create_definition(self.channel(channel_idx), fields)
        return Response(dump(definition), status=201)


class AttributeDetailView(AdminView):
    access_area = "leads.settings"

    def definition(self, channel_idx: str, pk: int) -> AttributeDefinition:
        return self.get_in(attribute_service.list_definitions(self.channel(channel_idx)), pk, "Attribute")

    @extend_schema(
        tags=_TAGS, summary="One attribute definition", responses={200: AttributeDefinitionResponse, **ERROR_RESPONSES}
    )
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        return Response(dump(self.definition(channel_idx, pk)))

    @extend_schema(
        tags=_TAGS,
        summary="Update an attribute definition",
        description="Entity and key are fixed; changing the kind is a 409 once a value is stored.",
        request=AttributeDefinitionUpdateRequest,
        responses={200: AttributeDefinitionResponse, **ERROR_RESPONSES, 409: None},
    )
    def patch(self, request: Request, channel_idx: str, pk: int) -> Response:
        updates = parse(AttributeDefinitionUpdateRequest, request.data).model_dump(mode="json", exclude_unset=True)
        if None in updates.values():
            raise ValidationError({"detail": ["fields may not be null"]})
        with service_errors():
            definition = attribute_service.update_definition(self.definition(channel_idx, pk), updates)
        return Response(dump(definition))

    @extend_schema(
        tags=_TAGS,
        summary="Delete an attribute definition",
        description="409 while values are stored — deactivate it instead.",
        responses={204: None, **ERROR_RESPONSES, 409: None},
    )
    def delete(self, request: Request, channel_idx: str, pk: int) -> Response:
        with service_errors():
            attribute_service.delete_definition(self.definition(channel_idx, pk))
        return Response(status=204)


class FieldListView(AdminView):
    access_area = "leads.settings"

    @extend_schema(
        tags=_TAGS,
        operation_id="leads_fields_list",
        summary="Facts rule conditions and scoring rules may name",
        description="Built-in facts, then the active company attributes as attr.<key>, with kinds and options.",
        responses={200: FieldListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        return Response({"results": fact_service.fields(self.channel(channel_idx))})
