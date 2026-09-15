# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — contacts: list, create, detail, patch. Email is immutable."""

from django_regional.models import Language
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, PAGE_PARAMETERS, AdminView, Conflict, parse
from django_leads.enums import LeadSource
from django_leads.models import Company, Contact
from django_leads.schemas.requests import ContactCreateRequest, ContactListQuery, ContactUpdateRequest
from django_leads.schemas.responses import ContactListResponse, ContactResponse
from django_leads.services import contact_service

_TAGS = ["Leads contacts"]
TEXT_FIELDS = ("first_name", "last_name", "job_title", "phone")


def resolve_language(values: dict) -> dict:
    """ISO code → Language; an unknown code is a 400."""
    if values.get("language") is None:
        return values
    language = Language.objects.filter(iso2__iexact=values["language"]).first()
    if language is None:
        raise ValidationError({"language": ["unknown language"]})
    return {**values, "language": language}


class ContactListView(AdminView):
    @extend_schema(
        tags=_TAGS,
        operation_id="leads_contacts_list",
        summary="Contacts of the channel",
        parameters=[OpenApiParameter("company", int, description="Company id."), *PAGE_PARAMETERS],
        responses={200: ContactListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(ContactListQuery, request.query_params.dict())
        contacts = contact_service.list_contacts(self.channel(channel_idx), company_id=query.company)
        return self.paginated(request, contacts, ContactResponse.of)

    @extend_schema(
        tags=_TAGS,
        summary="Add a contact to a company",
        request=ContactCreateRequest,
        responses={201: ContactResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(ContactCreateRequest, request.data).model_dump(mode="json")
        company = self.get_in(
            Company.objects.filter(channel=self.channel(channel_idx)), body.pop("company_id"), "Company"
        )
        consent_ref = body.pop("consent_ref")
        row = {**resolve_language(body), "email": body["email"] or "", "source": LeadSource.MANUAL}
        try:
            contact = contact_service.create_contact(company, row, actor=request.user.username, consent_ref=consent_ref)
        except contact_service.ContactExists as error:
            raise Conflict(str(error), code="contact_exists") from None
        return Response(ContactResponse.of(contact).model_dump(mode="json"), status=201)


class ContactDetailView(AdminView):
    def contact(self, channel_idx: str, pk: int) -> Contact:
        contacts = contact_service.list_contacts(self.channel(channel_idx))
        return self.get_in(contacts, pk, "Contact")

    @extend_schema(tags=_TAGS, summary="One contact", responses={200: ContactResponse, **ERROR_RESPONSES})
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        return Response(ContactResponse.of(self.contact(channel_idx, pk)).model_dump(mode="json"))

    @extend_schema(
        tags=_TAGS,
        summary="Update editable contact fields",
        request=ContactUpdateRequest,
        responses={200: ContactResponse, **ERROR_RESPONSES},
    )
    def patch(self, request: Request, channel_idx: str, pk: int) -> Response:
        updates = parse(ContactUpdateRequest, request.data).model_dump(mode="json", exclude_unset=True)
        if any(updates.get(field, "") is None for field in (*TEXT_FIELDS, "is_primary")):
            raise ValidationError({"detail": ["null is only allowed for language and legal_basis"]})
        consent_ref = updates.pop("consent_ref", None)
        contact = contact_service.update_contact(
            self.contact(channel_idx, pk),
            resolve_language(updates),
            actor=request.user.username,
            consent_ref=consent_ref,
        )
        return Response(ContactResponse.of(contact).model_dump(mode="json"))
