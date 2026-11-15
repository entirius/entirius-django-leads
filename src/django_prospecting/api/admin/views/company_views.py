# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — Companies (placeholder): rows added by hand; no search for new companies runs yet."""

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from django_prospecting.api.admin.views._base import ERROR_RESPONSES, TAGS, CompaniesView, parse
from django_prospecting.schemas.requests import CompanyCreateRequest
from django_prospecting.schemas.responses import CompanyListResponse, CompanyResponse
from django_prospecting.services import company_service

DESCRIPTION = (
    "Finding new companies is not built yet: this list keeps companies added by hand. Later sources: public "
    "registers (REGON, KRS) and site intelligence."
)


class CompanyListView(CompaniesView):
    @extend_schema(
        tags=TAGS,
        operation_id="prospecting_companies_list",
        summary="Companies (placeholder)",
        description=DESCRIPTION,
        responses={200: CompanyListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        rows = company_service.list_companies(channel_idx)[:100]
        payload = CompanyListResponse(
            description=DESCRIPTION, results=[CompanyResponse.model_validate(row) for row in rows]
        )
        return Response(payload.model_dump(mode="json"))

    @extend_schema(
        tags=TAGS,
        operation_id="prospecting_companies_create",
        summary="Add a company by hand (placeholder)",
        request=CompanyCreateRequest,
        responses={201: CompanyResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(CompanyCreateRequest, request.data)
        company = company_service.create(channel_idx=channel_idx, **body.model_dump())
        return Response(CompanyResponse.model_validate(company).model_dump(mode="json"), status=status.HTTP_201_CREATED)
