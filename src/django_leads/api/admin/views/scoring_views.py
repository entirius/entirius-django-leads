# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — scoring profiles (CRUD) and scoring one company now."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, AdminView, Conflict, parse
from django_leads.models import Company, ScoringProfile
from django_leads.schemas.requests import ScoringProfileRequest, ScoringProfileUpdateRequest
from django_leads.schemas.responses import ScoreResponse, ScoringProfileListResponse, ScoringProfileResponse
from django_leads.services import scoring_service

_TAGS = ["Leads Scoring"]


def dump(profile: ScoringProfile) -> dict:
    return ScoringProfileResponse.model_validate(profile).model_dump(mode="json")


@contextmanager
def service_errors() -> Iterator[None]:
    try:
        yield
    except scoring_service.ProfileExists as error:
        raise Conflict(str(error), code="profile_exists") from None
    except ValueError as error:
        raise ValidationError({"detail": [str(error)]}) from None


class ScoringProfileListView(AdminView):
    access_area = "leads.settings"

    @extend_schema(
        tags=_TAGS,
        operation_id="leads_scoring_profiles_list",
        summary="Scoring profiles of the channel",
        responses={200: ScoringProfileListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        profiles = scoring_service.list_profiles(self.channel(channel_idx))
        return Response({"results": [dump(profile) for profile in profiles]})

    @extend_schema(
        tags=_TAGS,
        summary="Add a scoring profile",
        description="Every rule and disqualifier must name a field of `fields/` with an op its kind takes (400).",
        request=ScoringProfileRequest,
        responses={201: ScoringProfileResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        fields = parse(ScoringProfileRequest, request.data).model_dump(mode="json")
        with service_errors():
            profile = scoring_service.save_profile(ScoringProfile(channel=self.channel(channel_idx)), fields)
        return Response(dump(profile), status=201)


class ScoringProfileDetailView(AdminView):
    access_area = "leads.settings"

    def profile(self, channel_idx: str, pk: int) -> ScoringProfile:
        return self.get_in(scoring_service.list_profiles(self.channel(channel_idx)), pk, "Scoring profile")

    @extend_schema(
        tags=_TAGS, summary="One scoring profile", responses={200: ScoringProfileResponse, **ERROR_RESPONSES}
    )
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        return Response(dump(self.profile(channel_idx, pk)))

    @extend_schema(
        tags=_TAGS,
        summary="Update a scoring profile",
        request=ScoringProfileUpdateRequest,
        responses={200: ScoringProfileResponse, **ERROR_RESPONSES, 409: None},
    )
    def patch(self, request: Request, channel_idx: str, pk: int) -> Response:
        updates = parse(ScoringProfileUpdateRequest, request.data).model_dump(mode="json", exclude_unset=True)
        if None in updates.values():
            raise ValidationError({"detail": ["fields may not be null"]})
        with service_errors():
            profile = scoring_service.save_profile(self.profile(channel_idx, pk), updates)
        return Response(dump(profile))

    @extend_schema(tags=_TAGS, summary="Delete a scoring profile", responses={204: None, **ERROR_RESPONSES})
    def delete(self, request: Request, channel_idx: str, pk: int) -> Response:
        self.profile(channel_idx, pk).delete()
        return Response(status=204)


class CompanyScoreView(AdminView):
    access_area = "leads.companies"

    @extend_schema(
        tags=_TAGS,
        summary="Score a company now",
        description="Stores score_points, score_segment and scored_at and writes the reasons to the timeline; "
        "409 NO_SCORING_PROFILE when the channel has no active profile.",
        request=None,
        responses={200: ScoreResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        companies = Company.objects.filter(channel=self.channel(channel_idx)).select_related("stage", "channel")
        company = self.get_in(companies, pk, "Company")
        try:
            result = scoring_service.score_and_record(company, actor=request.user.username)
        except scoring_service.NoScoringProfile as error:
            raise Conflict(str(error), code="no_scoring_profile") from None
        return Response(ScoreResponse.model_validate(asdict(result)).model_dump(mode="json"))
