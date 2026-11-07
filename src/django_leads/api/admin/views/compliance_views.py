# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — Q3/Q5/Q7: sender routes, address verification, the shared do-not-contact list, the send check."""

from django.db.models import Count
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads import settings as leads_settings
from django_leads.api.admin.views._base import ERROR_RESPONSES, AdminView, parse
from django_leads.models import Contact
from django_leads.schemas.requests import BulkSenderRouteRequest, ChannelSettingsRequest, DoNotContactRequest
from django_leads.schemas.responses import (
    BulkResultResponse,
    ChannelSettingsResponse,
    ContactResponse,
    DoNotContactAddedResponse,
    DoNotContactListResponse,
    SendCheckResponse,
    VerificationReportResponse,
)
from django_leads.services import (
    compliance_service,
    contact_service,
    dnc_service,
    sender_route_service,
    verification_service,
)

_TAGS = ["Leads Compliance"]


class _ContactView(AdminView):
    access_area = "leads.companies"

    def contact(self, channel_idx: str, pk: int) -> Contact:
        contacts = contact_service.list_contacts(self.channel(channel_idx)).select_related(
            "company__channel", "company__stage"
        )
        return self.get_in(contacts, pk, "Contact")


class SendCheckView(_ContactView):
    @extend_schema(
        tags=_TAGS,
        summary="Why this contact is or is not mailable",
        description="All gate reasons at once plus the legal basis and its source, the address status, the retention "
        "due date and the effective sender route. Read-only.",
        responses={200: SendCheckResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        body = compliance_service.explain(self.contact(channel_idx, pk))
        return Response(SendCheckResponse(**body).model_dump(mode="json"))


class VerifyContactView(_ContactView):
    @extend_schema(
        tags=_TAGS,
        summary="Verify the contact's address now",
        description="Runs the check (syntax, disposable, DNS/MX, optional probe) even when the loop is switched off.",
        request=None,
        responses={200: ContactResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        contact = self.contact(channel_idx, pk)
        if not verification_service.verifiable(contact):
            raise ValidationError({"detail": ["the contact has no address to verify"]})
        verification_service.verify_contact(contact, actor=f"verification:{request.user.username}")
        return Response(ContactResponse.of(contact).model_dump(mode="json"))


class VerificationReportView(AdminView):
    access_area = "leads.companies"

    @extend_schema(
        tags=_TAGS,
        summary="Address verification state of the channel",
        responses={200: VerificationReportResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        rows = Contact.objects.filter(company__channel=self.channel(channel_idx)).exclude(email="")
        counts = dict(rows.values_list("email_status").annotate(n=Count("id")))
        stale = verification_service.stale_contacts().filter(company__channel__idx=channel_idx).count()
        body = VerificationReportResponse(
            enabled=leads_settings.LEADS_VERIFY_ENABLED,
            max_age_days=leads_settings.LEADS_VERIFY_MAX_AGE_DAYS,
            counts=counts,
            stale=stale,
        )
        return Response(body.model_dump(mode="json"))


class BulkSenderRouteView(AdminView):
    access_area = "leads.companies"

    @extend_schema(
        tags=_TAGS,
        summary="Set the sender route of the chosen companies",
        request=BulkSenderRouteRequest,
        responses={200: BulkResultResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(BulkSenderRouteRequest, request.data)
        updated = sender_route_service.bulk_set(
            self.channel(channel_idx), body.company_ids, body.sender_route, actor=request.user.username
        )
        return Response(BulkResultResponse(updated=updated).model_dump())


def _settings_body(channel) -> dict:
    return ChannelSettingsResponse(
        default_sender_route=channel.default_sender_route,
        resume_days=channel.resume_days or {},
        resume_defaults=leads_settings.LEADS_RESUME_DEFAULT_DAYS,
    ).model_dump()


class ChannelSettingsView(AdminView):
    access_area = "leads.settings"

    @extend_schema(
        tags=_TAGS,
        summary='Channel settings: default sender route and "not now" windows',
        responses={200: ChannelSettingsResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        channel = self.channel(channel_idx)
        return Response(_settings_body(channel))

    @extend_schema(
        tags=_TAGS,
        summary='Set the channel\'s default sender route and the "not now" windows',
        request=ChannelSettingsRequest,
        responses={200: ChannelSettingsResponse, **ERROR_RESPONSES},
    )
    def patch(self, request: Request, channel_idx: str) -> Response:
        body = parse(ChannelSettingsRequest, request.data)
        channel = sender_route_service.update_settings(
            self.channel(channel_idx), default_sender_route=body.default_sender_route, resume_days=body.resume_days
        )
        return Response(_settings_body(channel))


class DoNotContactView(AdminView):
    access_area = "leads.companies"

    @extend_schema(
        tags=_TAGS,
        summary="The shared do-not-contact list",
        responses={200: DoNotContactListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        self.channel(channel_idx)
        return Response(DoNotContactListResponse(**dnc_service.listing()).model_dump(mode="json"))

    @extend_schema(
        tags=_TAGS,
        summary="Stop an address or a domain in every channel and mailbox",
        description="There is no endpoint to remove an entry: lifting a refusal is a decision for a person and the law.",
        request=DoNotContactRequest,
        responses={201: DoNotContactAddedResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        self.channel(channel_idx)
        body = parse(DoNotContactRequest, request.data)
        try:
            added = dnc_service.add(
                email=body.email.strip(), domain=body.domain.strip(), reason=body.reason, actor=request.user.username
            )
        except ValueError as error:
            raise ValidationError({"detail": [str(error)]}) from None
        return Response(DoNotContactAddedResponse(**added).model_dump(), status=201)
