# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — deal stages: CRUD of the channel's sales pipeline; a stage holding deals cannot be deleted (409)."""

from collections.abc import Iterator
from contextlib import contextmanager

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, AdminView, Conflict, parse
from django_leads.models import DealStage
from django_leads.schemas.requests import DealStageRequest, DealStageUpdateRequest
from django_leads.schemas.responses import DealStageListResponse, DealStageResponse
from django_leads.services import deal_stage_service

_TAGS = ["Leads Deal Stages"]


def dump(stage: DealStage) -> dict:
    return DealStageResponse.model_validate(stage).model_dump(mode="json")


@contextmanager
def service_errors() -> Iterator[None]:
    try:
        yield
    except deal_stage_service.DealStageExists as error:
        raise Conflict(str(error), code="deal_stage_exists") from None
    except deal_stage_service.DealStageInUse as error:
        raise Conflict(str(error), code="deal_stage_not_empty") from None


class DealStageListView(AdminView):
    access_area = "leads.settings"

    @extend_schema(
        tags=_TAGS,
        operation_id="leads_deal_stages_list",
        summary="Deal stages of the channel in pipeline order",
        responses={200: DealStageListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        stages = deal_stage_service.list_stages(self.channel(channel_idx))
        return Response({"results": [dump(stage) for stage in stages]})

    @extend_schema(
        tags=_TAGS,
        summary="Add a deal stage",
        description="Deals need at least one stage of each kind: a new deal starts in the first open stage.",
        request=DealStageRequest,
        responses={201: DealStageResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        fields = parse(DealStageRequest, request.data).model_dump(mode="json")
        with service_errors():
            stage = deal_stage_service.create_stage(self.channel(channel_idx), fields)
        return Response(dump(stage), status=201)


class DealStageDetailView(AdminView):
    access_area = "leads.settings"

    def stage(self, channel_idx: str, pk: int) -> DealStage:
        return self.get_in(deal_stage_service.list_stages(self.channel(channel_idx)), pk, "Deal stage")

    @extend_schema(tags=_TAGS, summary="One deal stage", responses={200: DealStageResponse, **ERROR_RESPONSES})
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        return Response(dump(self.stage(channel_idx, pk)))

    @extend_schema(
        tags=_TAGS,
        summary="Update a deal stage",
        description="409 on a key in use, or on a new kind while the stage holds deals.",
        request=DealStageUpdateRequest,
        responses={200: DealStageResponse, **ERROR_RESPONSES, 409: None},
    )
    def patch(self, request: Request, channel_idx: str, pk: int) -> Response:
        updates = parse(DealStageUpdateRequest, request.data).model_dump(mode="json", exclude_unset=True)
        if None in updates.values():
            raise ValidationError({"detail": ["fields may not be null"]})
        with service_errors():
            stage = deal_stage_service.update_stage(self.stage(channel_idx, pk), updates)
        return Response(dump(stage))

    @extend_schema(
        tags=_TAGS,
        summary="Delete a deal stage",
        description="409 while deals sit in the stage — move them first.",
        responses={204: None, **ERROR_RESPONSES, 409: None},
    )
    def delete(self, request: Request, channel_idx: str, pk: int) -> Response:
        with service_errors():
            deal_stage_service.delete_stage(self.stage(channel_idx, pk))
        return Response(status=204)
