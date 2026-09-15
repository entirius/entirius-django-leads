# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — GDPR export and erasure by email, channel-independent (`api/leads/v2/admin/gdpr/`)."""

from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, AdminView, parse
from django_leads.schemas.requests import GdprRequest
from django_leads.schemas.responses import GdprEraseResponse, GdprExportResponse
from django_leads.services import gdpr_service

_TAGS = ["Leads GDPR"]
_ERRORS = {key: value for key, value in ERROR_RESPONSES.items() if key != 404}


class GdprExportView(AdminView):
    @extend_schema(
        tags=_TAGS,
        summary="Export everything held about an email (GDPR art. 15)",
        description="Leads, communicator, agreements and any other installed app with GDPR hooks.",
        request=GdprRequest,
        responses={200: GdprExportResponse, **_ERRORS},
    )
    def post(self, request: Request) -> Response:
        body = parse(GdprRequest, request.data)
        return Response(gdpr_service.export(body.email))


class GdprEraseView(AdminView):
    @extend_schema(
        tags=_TAGS,
        summary="Erase everything held about an email (GDPR art. 17)",
        description="Pseudonymises in every module (rows stay), suppresses the address in communicator and logs "
        "the request on every affected company. Irreversible.",
        request=GdprRequest,
        responses={200: GdprEraseResponse, **_ERRORS},
    )
    def post(self, request: Request) -> Response:
        body = parse(GdprRequest, request.data)
        counts = gdpr_service.erase(body.email, actor=request.user.username)
        return Response(GdprEraseResponse(modules=counts).model_dump())
