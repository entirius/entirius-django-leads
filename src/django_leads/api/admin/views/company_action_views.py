# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — manual company actions: draft through communicator, request a siteintel audit."""

from django_communicator.models import Channel as CommunicatorChannel
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, AdminView, Conflict, parse
from django_leads.models import Company
from django_leads.schemas.requests import CommunicateRequest
from django_leads.schemas.responses import AuditRequestedResponse, DraftResponse
from django_leads.services import intel_service, outreach_service

_TAGS = ["Leads companies"]


class CompanyActionView(AdminView):
    def company(self, channel_idx: str, pk: int) -> Company:
        companies = Company.objects.filter(channel=self.channel(channel_idx)).select_related("channel")
        return self.get_in(companies, pk, "Company")


class CompanyCommunicateView(CompanyActionView):
    @extend_schema(
        tags=_TAGS,
        summary="Request a reviewable draft for a contact",
        description="No rule conditions or cooldown. 409 for do_not_contact or when no draft can be built "
        "(no clause set, no legal basis for a template with a legal footer); 400 for a foreign or email-less contact.",
        request=CommunicateRequest,
        responses={201: DraftResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        body = parse(CommunicateRequest, request.data)
        company = self.company(channel_idx, pk)
        if company.do_not_contact:
            raise Conflict("company is do_not_contact")
        contact = company.contacts.exclude(email="").filter(pk=body.contact_id).first()
        if contact is None:
            raise ValidationError({"contact_id": ["no contact with an email in this company"]})
        try:
            message = outreach_service.request_draft(company, contact, body.template_key, actor=request.user.username)
        except CommunicatorChannel.DoesNotExist:
            raise Conflict("communicator channel not configured") from None
        if message is None:
            raise Conflict("no draft: see the company timeline")
        return Response(DraftResponse(message_id=message.pk, status=message.status).model_dump(), status=201)


class CompanyRequestAuditView(CompanyActionView):
    @extend_schema(
        tags=_TAGS,
        summary="Request a siteintel audit of the company domain",
        request=None,
        responses={202: AuditRequestedResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        audit = intel_service.request_audit_for(self.company(channel_idx, pk), actor=request.user.username)
        return Response(AuditRequestedResponse(audit_id=str(audit.pk), status=audit.status).model_dump(), status=202)
