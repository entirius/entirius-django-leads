# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — companies: list, create, detail, patch, stage transition."""

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, PAGE_PARAMETERS, AdminView, Conflict, parse
from django_leads.enums import LeadSource
from django_leads.models import Company, Stage
from django_leads.schemas.requests import (
    CompanyCreateRequest,
    CompanyListQuery,
    CompanyUpdateRequest,
    TransitionRequest,
)
from django_leads.schemas.responses import (
    ActivityResponse,
    CompanyDetailResponse,
    CompanyListResponse,
    CompanyResponse,
    ContactResponse,
)
from django_leads.services import company_service, stage_service

_TAGS = ["Leads companies"]
RECENT_ACTIVITIES = 20


def detail(company: Company) -> dict:
    contacts = [ContactResponse.of(contact) for contact in company.contacts.select_related("language")]
    activities = [ActivityResponse.model_validate(row) for row in company.activities.all()[:RECENT_ACTIVITIES]]
    body = CompanyDetailResponse(**CompanyResponse.of(company).model_dump(), contacts=contacts, activities=activities)
    return body.model_dump(mode="json")


class CompanyListView(AdminView):
    @extend_schema(
        tags=_TAGS,
        operation_id="leads_companies_list",
        summary="Companies of the channel",
        parameters=[
            OpenApiParameter("stage", str, description="Stage key."),
            OpenApiParameter("search", str, description="Substring of name or domain."),
            OpenApiParameter("sort", str, description="name, domain, stage_entered_at, last_activity_at; `-` prefix."),
            *PAGE_PARAMETERS,
        ],
        responses={200: CompanyListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(CompanyListQuery, request.query_params.dict())
        companies = company_service.list_companies(self.channel(channel_idx), **query.model_dump())
        return self.paginated(request, companies, CompanyResponse.of)

    @extend_schema(
        tags=_TAGS,
        summary="Create a company by hand",
        description="The domain is stored registrable; an existing domain in the channel is a 409.",
        request=CompanyCreateRequest,
        responses={201: CompanyDetailResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(CompanyCreateRequest, request.data)
        row = {**body.model_dump(mode="json"), "source": LeadSource.MANUAL}
        try:
            company = company_service.create_company(self.channel(channel_idx), row, actor=request.user.username)
        except company_service.CompanyExists as error:
            raise Conflict(str(error)) from None
        except stage_service.NoStages as error:
            raise Conflict(str(error)) from None
        return Response(detail(company), status=201)


class CompanyDetailView(AdminView):
    def company(self, channel_idx: str, pk: int) -> Company:
        companies = Company.objects.filter(channel=self.channel(channel_idx)).select_related("stage")
        return self.get_in(companies, pk, "Company")

    @extend_schema(
        tags=_TAGS,
        summary="One company with contacts and its last 20 activities",
        responses={200: CompanyDetailResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        return Response(detail(self.company(channel_idx, pk)))

    @extend_schema(
        tags=_TAGS,
        summary="Update editable company fields",
        description="Stage, domain, hooks and customer link are not editable here.",
        request=CompanyUpdateRequest,
        responses={200: CompanyDetailResponse, **ERROR_RESPONSES},
    )
    def patch(self, request: Request, channel_idx: str, pk: int) -> Response:
        updates = parse(CompanyUpdateRequest, request.data).model_dump(mode="json", exclude_unset=True)
        updates = {field: "" if value is None and field == "website" else value for field, value in updates.items()}
        if None in updates.values():
            raise ValidationError({"detail": ["null is only allowed for website"]})
        company = company_service.update_company(self.company(channel_idx, pk), updates)
        return Response(detail(company))


class CompanyTransitionView(AdminView):
    @extend_schema(
        tags=_TAGS,
        summary="Move a company to another stage",
        request=TransitionRequest,
        responses={200: CompanyDetailResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        body = parse(TransitionRequest, request.data)
        channel = self.channel(channel_idx)
        company = self.get_in(Company.objects.filter(channel=channel).select_related("stage"), pk, "Company")
        stage = Stage.objects.filter(channel=channel, key=body.stage_key).first()
        if stage is None:
            raise NotFound("Stage not found.")
        company = stage_service.transition_stage(company, stage, actor=request.user.username)
        return Response(detail(company))
