# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — deals: list, board, create, detail, patch, delete, stage transition, archive, restore, timeline."""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager

from django.db import transaction
from django.db.models import QuerySet
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import (
    ERROR_RESPONSES,
    PAGE_PARAMETERS,
    AdminPagination,
    AdminView,
    Conflict,
    parse,
)
from django_leads.api.admin.views.deal_stage_views import dump as dump_stage
from django_leads.api.admin.views.owner_views import owner_or_400
from django_leads.models import Channel, Company, Deal, DealStage
from django_leads.schemas.requests import (
    OWNER_FILTER_DESCRIPTION,
    DealBoardQuery,
    DealCreateRequest,
    DealListQuery,
    DealTransitionRequest,
    DealUpdateRequest,
)
from django_leads.schemas.responses import (
    ActivityListResponse,
    ActivityResponse,
    DealBoardResponse,
    DealListResponse,
    DealResponse,
)
from django_leads.services import (
    activity_service,
    attribute_service,
    deal_board_service,
    deal_service,
    deal_stage_service,
    owner_service,
    task_bridge,
)
from django_leads.utils.refs import deal_ref

_TAGS = ["Leads Deals"]
OWNER_ID = "owner_id"
LIST_PARAMETERS = [
    OpenApiParameter("stage", str, description="Deal stage key."),
    OpenApiParameter("kind", str, description="open, won or lost."),
    OpenApiParameter("owner", str, description=OWNER_FILTER_DESCRIPTION),
    OpenApiParameter("company", int, description="Company id."),
    OpenApiParameter("search", str, description="Substring of the title or the company's name or domain."),
    OpenApiParameter("close_from", str, description="Expected close date on or after (YYYY-MM-DD)."),
    OpenApiParameter("close_to", str, description="Expected close date on or before (YYYY-MM-DD)."),
    OpenApiParameter("archived", bool, description="true = only archived deals; default false = only the others."),
    OpenApiParameter(
        "sort",
        str,
        description="expected_close_date, stage_entered_at, value_one_off, value_recurring, created; `-` prefix.",
    ),
    *PAGE_PARAMETERS,
]


def dump_many(channel: Channel, deals: Iterable[Deal]) -> list[dict]:
    """Deals with company, contact, owner, stage and currency joined; open tasks from one query."""
    refs = {deal.pk: deal_ref(deal) for deal in deals}
    tasks = task_bridge.open_counts(channel.idx, list(refs.values()))
    return [
        DealResponse.of(
            deal,
            open_tasks=tasks.get(refs[deal.pk], 0),
            subject_ref=refs[deal.pk],
            subject_label=deal_service.subject_label(deal),
        ).model_dump(mode="json")
        for deal in deals
    ]


@contextmanager
def service_errors() -> Iterator[None]:
    try:
        yield
    except deal_service.InvalidDeal as error:
        raise ValidationError({error.field: [str(error)]}) from None
    except attribute_service.InvalidAttribute as error:
        raise ValidationError({"attributes": [str(error)]}) from None
    except deal_stage_service.MissingDealStage as error:
        raise Conflict(str(error), code="no_deal_stage") from None
    except deal_service.DealClosed as error:
        raise Conflict(str(error), code="deal_closed") from None
    except deal_service.DealOpen as error:
        raise Conflict(str(error), code="deal_open") from None
    except deal_service.DealArchived as error:
        raise Conflict(str(error), code="deal_archived") from None
    except Deal.DoesNotExist:
        raise NotFound("Deal not found.") from None


class DealView(AdminView):
    access_area = "leads.companies"

    def page(self, request: Request, channel: Channel, deals: QuerySet) -> Response:
        paginator = AdminPagination()
        rows = paginator.paginate_queryset(deals, request, view=self)
        return paginator.get_paginated_response(dump_many(channel, rows))

    def deal(self, channel: Channel, pk: int) -> Deal:
        return self.get_in(Deal.objects.filter(channel=channel).select_related(*deal_service.RELATED), pk, "Deal")

    def one(self, channel: Channel, deal: Deal) -> dict:
        return dump_many(channel, [self.deal(channel, deal.pk)])[0]


class DealListView(DealView):
    @extend_schema(
        tags=_TAGS,
        operation_id="leads_deals_list",
        summary="Deals of the channel",
        parameters=LIST_PARAMETERS,
        responses={200: DealListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(DealListQuery, request.query_params.dict())
        channel = self.channel(channel_idx)
        deals = deal_service.list_deals(channel, **query.model_dump(), user_id=request.user.pk)
        return self.page(request, channel, deals)

    @extend_schema(
        tags=_TAGS,
        summary="Open a deal on a company",
        description="Starts in the first open deal stage (409 without one). Without `owner_id` the company's owner "
        "owns it, else the requesting user; without `currency` the channel default.",
        request=DealCreateRequest,
        responses={201: DealResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(DealCreateRequest, request.data)
        channel = self.channel(channel_idx)
        company = Company.objects.filter(channel=channel, pk=body.company_id).select_related("owner").first()
        if company is None:
            raise ValidationError({"company_id": ["not a company of this channel"]})
        named = OWNER_ID in body.model_fields_set
        owner = owner_or_400(body.owner_id) if named else deal_service.default_owner(company, request.user)
        fields = body.model_dump(exclude={"company_id", OWNER_ID}, exclude_unset=True)  # unset currency = default
        with service_errors():
            deal = deal_service.create(company, fields, owner=owner, actor=request.user.username)
        return Response(self.one(channel, deal), status=201)


class DealBoardView(DealView):
    @extend_schema(
        tags=_TAGS,
        operation_id="leads_deals_board",
        summary="Deal board: one column per deal stage",
        description="Count, totals per currency (recurring per month) and the first 25 deals of every stage.",
        parameters=[OpenApiParameter("owner", str, description=OWNER_FILTER_DESCRIPTION)],
        responses={200: DealBoardResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(DealBoardQuery, request.query_params.dict())
        channel = self.channel(channel_idx)
        columns = deal_board_service.board(channel, owner=query.owner, user_id=request.user.pk)
        rows = dump_many(channel, [deal for column in columns for deal in column.deals])
        return Response({"results": [self.column(column, rows) for column in columns]})

    @staticmethod
    def column(column: deal_board_service.Column, rows: list[dict]) -> dict:
        ids = {deal.pk for deal in column.deals}
        return {
            "stage": dump_stage(column.stage),
            "count": column.count,
            "totals": [
                {"currency": total.currency, "one_off": str(total.one_off), "recurring": str(total.recurring)}
                for total in column.totals
            ],
            "deals": [row for row in rows if row["id"] in ids],
        }


class DealDetailView(DealView):
    @extend_schema(tags=_TAGS, summary="One deal", responses={200: DealResponse, **ERROR_RESPONSES})
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        channel = self.channel(channel_idx)
        return Response(dump_many(channel, [self.deal(channel, pk)])[0])

    @extend_schema(
        tags=_TAGS,
        summary="Update a deal",
        description="Stage moves through transition/; `attributes` is merged (null removes a key); `owner_id` sets the "
        "owner (null clears it). A value needs a currency; amounts are never negative. 409 for an archived deal.",
        request=DealUpdateRequest,
        responses={200: DealResponse, **ERROR_RESPONSES, 409: None},
    )
    def patch(self, request: Request, channel_idx: str, pk: int) -> Response:
        updates = parse(DealUpdateRequest, request.data).model_dump(exclude_unset=True)
        owner_given, owner = OWNER_ID in updates, owner_or_400(updates.pop(OWNER_ID, None))
        channel, actor = self.channel(channel_idx), request.user.username
        deal = self.deal(channel, pk)
        with service_errors(), transaction.atomic():
            deal_service.update(deal, updates, actor=actor)
            if owner_given:
                owner_service.set_deal_owner(deal, owner, actor=actor)
        return Response(self.one(channel, deal))

    @extend_schema(
        tags=_TAGS,
        summary="Delete an open deal",
        description="409 for a won, lost or archived deal.",
        responses={204: None, **ERROR_RESPONSES, 409: None},
    )
    def delete(self, request: Request, channel_idx: str, pk: int) -> Response:
        deal = self.deal(self.channel(channel_idx), pk)
        with service_errors():
            deal_service.delete(deal, actor=request.user.username)
        return Response(status=204)


class DealTransitionView(DealView):
    @extend_schema(
        tags=_TAGS,
        summary="Move a deal to another deal stage",
        description="By `stage_key`, or by `kind` = the first stage of that kind (409 when the channel has none). A "
        "lost stage needs `lost_reason` (400). 409 for an archived deal.",
        request=DealTransitionRequest,
        responses={200: DealResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        body = parse(DealTransitionRequest, request.data)
        channel = self.channel(channel_idx)
        deal = self.deal(channel, pk)
        with service_errors():
            stage = self.target(channel, body)
            deal_service.transition(deal, stage, actor=request.user.username, lost_reason=body.lost_reason)
        return Response(self.one(channel, deal))

    @staticmethod
    def target(channel: Channel, body: DealTransitionRequest) -> DealStage:
        if body.kind is not None:
            return deal_stage_service.first_of_kind(channel, body.kind)
        try:
            return deal_stage_service.by_key(channel, body.stage_key)
        except deal_stage_service.MissingDealStage:
            raise NotFound("Deal stage not found.") from None


class DealArchiveView(DealView):
    @extend_schema(
        tags=_TAGS,
        summary="Archive a won or lost deal",
        description="Out of the lists, the board and the company's counts; still in the reports and the timeline. 409 "
        "for an open deal; an archived deal is returned as it is.",
        request=None,
        responses={200: DealResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        channel = self.channel(channel_idx)
        with service_errors():
            deal = deal_service.archive(self.deal(channel, pk), actor=request.user.username)
        return Response(self.one(channel, deal))


class DealRestoreView(DealView):
    @extend_schema(
        tags=_TAGS,
        summary="Restore an archived deal",
        description="Back in the lists and the board; a deal that is not archived is returned as it is.",
        request=None,
        responses={200: DealResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        channel = self.channel(channel_idx)
        with service_errors():
            deal = deal_service.restore(self.deal(channel, pk), actor=request.user.username)
        return Response(self.one(channel, deal))


class DealActivityListView(DealView):
    @extend_schema(
        tags=_TAGS,
        operation_id="leads_deal_activities_list",
        summary="Timeline entries about the deal, newest first",
        parameters=PAGE_PARAMETERS,
        responses={200: ActivityListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        deal = self.deal(self.channel(channel_idx), pk)
        return self.paginated(request, activity_service.list_for_deal(deal), ActivityResponse.model_validate)


class CompanyDealListView(DealView):
    @extend_schema(
        tags=_TAGS,
        operation_id="leads_company_deals_list",
        summary="Deals of one company, newest first",
        parameters=PAGE_PARAMETERS,
        responses={200: DealListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        channel = self.channel(channel_idx)
        company = self.get_in(Company.objects.filter(channel=channel), pk, "Company")
        return self.page(request, channel, deal_service.list_deals(channel, company=company.pk))
