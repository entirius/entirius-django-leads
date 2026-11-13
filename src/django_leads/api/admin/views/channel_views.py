# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — channel settings: days until a follow-up task per reply class, default deal currency."""

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, AdminView, parse
from django_leads.models import Channel
from django_leads.schemas.requests import ChannelSettingsRequest
from django_leads.schemas.responses import ChannelSettingsResponse
from django_leads.services import channel_service, deal_service, follow_up_service

_TAGS = ["Leads Channel"]


def _dump(channel: Channel) -> dict:
    resume_days = follow_up_service.resume_days(channel)
    currency = channel.default_currency.iso3 if channel.default_currency_id else None
    body = ChannelSettingsResponse(
        idx=channel.idx, name=channel.name, default_currency=currency, resume_days=resume_days
    )
    return body.model_dump()


class ChannelSettingsView(AdminView):
    access_area = "leads.settings"

    @extend_schema(
        tags=_TAGS,
        operation_id="leads_channel_settings_retrieve",
        summary="Channel settings",
        responses={200: ChannelSettingsResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        return Response(_dump(self.channel(channel_idx)))

    @extend_schema(
        tags=_TAGS,
        operation_id="leads_channel_settings_update",
        summary="Change channel settings",
        request=ChannelSettingsRequest,
        responses={200: ChannelSettingsResponse, **ERROR_RESPONSES},
    )
    def patch(self, request: Request, channel_idx: str) -> Response:
        body = parse(ChannelSettingsRequest, request.data)
        channel = self.channel(channel_idx)
        updates = {}
        if "resume_days" in body.model_fields_set:  # null resets to the defaults
            updates["resume_days"] = body.resume_days.model_dump(exclude_none=True) if body.resume_days else {}
        if "default_currency" in body.model_fields_set:
            updates["default_currency"] = body.default_currency
        try:
            return Response(_dump(channel_service.update_channel(channel, updates)))
        except deal_service.InvalidDeal as error:
            raise ValidationError({"default_currency": [str(error)]}) from None
