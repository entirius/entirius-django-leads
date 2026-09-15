# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — pipeline stages: CRUD; a stage holding companies cannot be deleted (409)."""

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, AdminView, Conflict, parse
from django_leads.models import Stage
from django_leads.schemas.requests import StageRequest, StageUpdateRequest
from django_leads.schemas.responses import StageListResponse, StageResponse
from django_leads.services import stage_service

_TAGS = ["Leads stages"]


def dump(stage: Stage) -> dict:
    return StageResponse.model_validate(stage).model_dump(mode="json")


class StageListView(AdminView):
    @extend_schema(
        tags=_TAGS,
        operation_id="leads_stages_list",
        summary="Stages of the channel in pipeline order",
        responses={200: StageListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        stages = stage_service.list_stages(self.channel(channel_idx))
        return Response({"results": [dump(stage) for stage in stages]})

    @extend_schema(
        tags=_TAGS,
        summary="Add a stage",
        request=StageRequest,
        responses={201: StageResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        fields = parse(StageRequest, request.data).model_dump(mode="json")
        try:
            stage = stage_service.create_stage(self.channel(channel_idx), fields)
        except stage_service.StageExists as error:
            raise Conflict(str(error), code="stage_exists") from None
        return Response(dump(stage), status=201)


class StageDetailView(AdminView):
    def stage(self, channel_idx: str, pk: int) -> Stage:
        return self.get_in(stage_service.list_stages(self.channel(channel_idx)), pk, "Stage")

    @extend_schema(tags=_TAGS, summary="One stage", responses={200: StageResponse, **ERROR_RESPONSES})
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        return Response(dump(self.stage(channel_idx, pk)))

    @extend_schema(
        tags=_TAGS,
        summary="Update a stage",
        request=StageUpdateRequest,
        responses={200: StageResponse, **ERROR_RESPONSES, 409: None},
    )
    def patch(self, request: Request, channel_idx: str, pk: int) -> Response:
        updates = parse(StageUpdateRequest, request.data).model_dump(mode="json", exclude_unset=True)
        if None in updates.values():
            raise ValidationError({"detail": ["fields may not be null"]})
        try:
            stage = stage_service.update_stage(self.stage(channel_idx, pk), updates)
        except stage_service.StageExists as error:
            raise Conflict(str(error), code="stage_exists") from None
        return Response(dump(stage))

    @extend_schema(
        tags=_TAGS,
        summary="Delete a stage",
        description="409 while companies sit in the stage — move them first.",
        responses={204: None, **ERROR_RESPONSES, 409: None},
    )
    def delete(self, request: Request, channel_idx: str, pk: int) -> Response:
        try:
            stage_service.delete_stage(self.stage(channel_idx, pk))
        except stage_service.StageInUse as error:
            raise Conflict(str(error), code="stage_not_empty") from None
        return Response(status=204)
