# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — manual company actions: draft through communicator, a hand-written mail, a siteintel audit."""

from django_communicator.models import Channel as CommunicatorChannel
from django_communicator.services.communicate_service import ThreadMismatchError
from django_communicator.services.compose_service import ComposeError
from django_utils.toolbox import ToolboxDisabledError, handle_toolbox_error
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, AdminView, Conflict, parse
from django_leads.models import Company
from django_leads.schemas.requests import CommunicateRequest, ComposeRequest
from django_leads.schemas.responses import AuditRequestedResponse, DraftResponse, MailableResponse
from django_leads.services import intel_service, mailable_service, outreach_service

_TAGS = ["Leads Companies"]


class CompanyActionView(AdminView):
    access_area = "leads.companies"

    def company(self, channel_idx: str, pk: int) -> Company:
        companies = Company.objects.filter(channel=self.channel(channel_idx)).select_related("channel")
        return self.get_in(companies, pk, "Company")

    @staticmethod
    def mailable_contact(company: Company, contact_id: int):
        contact = company.contacts.exclude(email="").filter(pk=contact_id).first()
        if contact is None:
            raise ValidationError({"contact_id": ["no contact with an email in this company"]})
        return contact


class CompanyCommunicateView(CompanyActionView):
    @extend_schema(
        tags=_TAGS,
        summary="Request a reviewable draft for a contact",
        description="No rule conditions or cooldown. 409 when the outreach gate refuses the contact (do_not_contact, "
        "opted_out, anonymised, wrong_person, no_legal_basis, suppressed, address_invalid) or no draft can be built (no clause set); 400 for a foreign or "
        "email-less contact; 503 `AI_TOOLBOX_DISABLED` for an AI template while the toolbox is off.",
        request=CommunicateRequest,
        responses={201: DraftResponse, **ERROR_RESPONSES, 409: None, 503: None},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        body = parse(CommunicateRequest, request.data)
        company = self.company(channel_idx, pk)
        contact = self.mailable_contact(company, body.contact_id)
        try:
            message = outreach_service.request_draft(company, contact, body.template_key, actor=request.user.username)
        except CommunicatorChannel.DoesNotExist:
            raise Conflict("communicator channel not configured", code="communicator_channel_missing") from None
        except ToolboxDisabledError as error:
            return handle_toolbox_error(error)
        if isinstance(message, outreach_service.Blocked):
            raise Conflict(f"contact not eligible: {message.reason}", code="not_eligible")
        if message is None:
            raise Conflict("no draft: see the company timeline", code="no_draft")
        return Response(DraftResponse(message_id=message.pk, status=message.status).model_dump(), status=201)


class CompanyComposeView(CompanyActionView):
    @extend_schema(
        tags=_TAGS,
        summary="Send a hand-written mail to a contact",
        description="The outreach gate first: 409 `not_eligible` with the reason (one `blocked` activity). Then the "
        "legal footer of the contact's basis and language (409 `no_legal_basis`) and communicator's compose from the "
        "owner's mailbox, approved by the caller and due now; it stops the thread's sequence. 409 "
        "`communicator_channel_missing`; 400 for a foreign or email-less contact, a reply of another company or "
        "another contact, a blank or too long text.",
        request=ComposeRequest,
        responses={201: DraftResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        body = parse(ComposeRequest, request.data)
        company = self.company(channel_idx, pk)
        contact = self.mailable_contact(company, body.contact_id)
        reply = self.reply(company, body.reply_id)
        try:
            message = outreach_service.compose_message(
                company, contact, subject=body.subject, body=body.body, actor=request.user, reply=reply
            )
        except CommunicatorChannel.DoesNotExist:
            raise Conflict("communicator channel not configured", code="communicator_channel_missing") from None
        except outreach_service.NoLegalBasis:
            raise Conflict("no legal footer for the contact", code="no_legal_basis") from None
        except ComposeError as error:
            raise ValidationError({"body": [error.client_message]}) from None
        except ThreadMismatchError:
            raise ValidationError({"reply_id": ["the reply belongs to another contact"]}) from None
        if isinstance(message, outreach_service.Blocked):
            raise Conflict(f"contact not eligible: {message.reason}", code="not_eligible")
        return Response(DraftResponse(message_id=message.pk, status=message.status).model_dump(), status=201)

    @staticmethod
    def reply(company: Company, reply_id: int | None):
        if reply_id is None:
            return None
        if (reply := outreach_service.reply_of(company, reply_id)) is None:
            raise ValidationError({"reply_id": ["no reply of this company"]})
        return reply


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


class ContactMailableView(CompanyActionView):
    @extend_schema(
        tags=_TAGS,
        summary="Why a contact may (not) be mailed",
        description="Every reason of the outreach gate, the legal basis and its source, communicator's suppression "
        "answer and the retention clock. Read only: the gate itself runs when a draft is requested.",
        responses={200: MailableResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str, pk: int, contact_id: int) -> Response:
        company = self.company(channel_idx, pk)
        contacts = company.contacts.select_related("company__channel", "company__stage")
        contact = self.get_in(contacts, contact_id, "Contact")
        body = MailableResponse.model_validate(mailable_service.mailable(contact))
        return Response(body.model_dump(mode="json"))
