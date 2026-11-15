# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — People: searches of a company, candidates with their four checks, accept and reject."""

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response

from django_prospecting import settings as prospecting_settings
from django_prospecting.api.admin.views._base import ERROR_RESPONSES, TAGS, PeopleView, decision_errors, parse
from django_prospecting.enums import SearchReason
from django_prospecting.models import Candidate
from django_prospecting.schemas.requests import (
    CandidateAcceptRequest,
    CandidateListQuery,
    CandidateRejectRequest,
    SearchCreateRequest,
    SearchListQuery,
)
from django_prospecting.schemas.responses import (
    CandidateListResponse,
    CandidateResponse,
    LimitsResponse,
    SearchListResponse,
    SearchResponse,
)
from django_prospecting.services import candidate_service, search_service

SUBJECT = OpenApiParameter("subject_ref", str, description="Only this company (`leads.Company:42`).")


def _candidate(channel_idx: str, pk: int) -> Candidate:
    try:
        return candidate_service.get_candidate(channel_idx, pk)
    except Candidate.DoesNotExist:
        raise NotFound("Candidate not found.") from None


def _candidate_payload(candidate: Candidate) -> Response:
    return Response(CandidateResponse.of(candidate).model_dump(mode="json"))


class SearchListView(PeopleView):
    @extend_schema(
        tags=TAGS,
        operation_id="prospecting_searches_list",
        summary="People searches of the channel",
        description="Newest first, at most 100.",
        parameters=[SUBJECT],
        responses={200: SearchListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(SearchListQuery, request.query_params.dict(), "query")
        rows = search_service.list_searches(channel_idx, query.subject_ref)[:100]
        payload = SearchListResponse(results=[SearchResponse.model_validate(row) for row in rows])
        return Response(payload.model_dump(mode="json"))

    @extend_schema(
        tags=TAGS,
        operation_id="prospecting_searches_create",
        summary="Look for new people at a company",
        description="Runs in the worker. A company with a queued search gets that one; over the company limit the "
        "search is `refused`.",
        request=SearchCreateRequest,
        responses={201: SearchResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(SearchCreateRequest, request.data)
        search = search_service.start(
            channel_idx=channel_idx, reason=SearchReason.MANUAL, requested_by=request.user.get_username(),
            **body.model_dump(),
        )  # fmt: skip
        return Response(SearchResponse.model_validate(search).model_dump(mode="json"), status=status.HTTP_201_CREATED)


class CandidateListView(PeopleView):
    @extend_schema(
        tags=TAGS,
        operation_id="prospecting_candidates_list",
        summary="Candidates with their checks",
        description="By company, then as found; at most 200.",
        parameters=[SUBJECT, OpenApiParameter("status", str, enum=["pending", "accepted", "rejected"])],
        responses={200: CandidateListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(CandidateListQuery, request.query_params.dict(), "query")
        rows = candidate_service.list_candidates(channel_idx, subject_ref=query.subject_ref, status=query.status)
        payload = CandidateListResponse(results=[CandidateResponse.of(row) for row in rows[:200]])
        return Response(payload.model_dump(mode="json"))


class CandidateAcceptView(PeopleView):
    @extend_schema(
        tags=TAGS,
        operation_id="prospecting_candidates_accept",
        summary="Accept a candidate: it becomes a contact in leads",
        request=CandidateAcceptRequest,
        responses={200: CandidateResponse, 409: None, 503: None, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        body = parse(CandidateAcceptRequest, request.data or {})
        with decision_errors("email"):
            candidate = candidate_service.accept(
                _candidate(channel_idx, pk), actor=request.user.get_username(), email=body.email
            )
        return _candidate_payload(candidate)


class CandidateRejectView(PeopleView):
    @extend_schema(
        tags=TAGS,
        operation_id="prospecting_candidates_reject",
        summary="Reject a candidate",
        request=CandidateRejectRequest,
        responses={200: CandidateResponse, 409: None, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        body = parse(CandidateRejectRequest, request.data or {})
        with decision_errors("reason"):
            candidate = candidate_service.reject(
                _candidate(channel_idx, pk), actor=request.user.get_username(), reason=body.reason
            )
        return _candidate_payload(candidate)


class LimitsView(PeopleView):
    @extend_schema(
        tags=TAGS,
        operation_id="prospecting_limits",
        summary="Search limits and today's spend",
        description="Read-only: the limits are host settings (`PROSPECTING_*`).",
        responses={200: LimitsResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        payload = LimitsResponse(
            search_provider=prospecting_settings.PROSPECTING_SEARCH_PROVIDER,
            address_tester=prospecting_settings.PROSPECTING_ADDRESS_TESTER,
            max_attempts=prospecting_settings.PROSPECTING_MAX_ATTEMPTS,
            company_search_limit=prospecting_settings.PROSPECTING_COMPANY_SEARCH_LIMIT,
            daily_budget=prospecting_settings.PROSPECTING_DAILY_BUDGET,
            spent_today=search_service.spent_today(channel_idx),
            max_candidates=prospecting_settings.PROSPECTING_MAX_CANDIDATES,
        )
        return Response(payload.model_dump(mode="json"))
