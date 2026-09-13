# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — won seam: link an existing accounts Customer (routed only with django_accounts)."""

from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES
from django_leads.api.admin.views.company_action_views import CompanyActionView
from django_leads.schemas.responses import CustomerLinkResponse
from django_leads.services import customer_link_service

NOT_IMPLEMENTED = {
    "error": "NotImplemented",
    "detail": "Customer creation is not implemented in v1 — create the account first, then link",
}


class CompanyCreateCustomerView(CompanyActionView):
    @extend_schema(
        tags=["Leads companies"],
        summary="Link the Customer owning the primary contact's email",
        description="409 NotImplemented when no Customer matches — v1 never creates accounts.",
        request=None,
        responses={200: CustomerLinkResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        company = self.company(channel_idx, pk)
        try:
            uid = customer_link_service.link_customer(company, actor=request.user.username)
        except customer_link_service.NoCustomer:
            return Response(NOT_IMPLEMENTED, status=409)
        return Response(CustomerLinkResponse(customer_uid=uid).model_dump())
