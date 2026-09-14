# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Development-only endpoints for BDD: synchronous CSV import, rule evaluation and rotation scan.

404 outside `ENVIRONMENT == "development"`.
"""

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.parsers import MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, AdminView, parse
from django_leads.api.admin.views.import_views import UPLOAD_SCHEMA, read_upload
from django_leads.enums import RuleTrigger
from django_leads.models import Company, Stage
from django_leads.schemas.requests import DevEvaluateRequest
from django_leads.schemas.responses import (
    DevEvaluateResponse,
    DevRotateResponse,
    ImportBatchDetailResponse,
    RuleRunResponse,
)
from django_leads.services import import_service, rotation_service, rule_service

_TAGS = ["Leads (development)"]


def is_development() -> bool:
    return getattr(settings, "ENVIRONMENT", "") == "development"


class DevelopmentView(AdminView):
    """Answers 404 outside `ENVIRONMENT == "development"`, even when a host mounts the URL."""

    def initial(self, request: Request, *args, **kwargs) -> None:
        if not is_development():
            raise NotFound()
        super().initial(request, *args, **kwargs)


class DevEvaluateView(DevelopmentView):
    @extend_schema(
        tags=_TAGS,
        summary="Evaluate the rules of a trigger now (development only)",
        request=DevEvaluateRequest,
        responses={200: DevEvaluateResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(DevEvaluateRequest, request.data)
        channel = self.channel(channel_idx)
        company = self.get_in(
            Company.objects.filter(channel=channel).select_related("channel"), body.company_id, "Company"
        )
        stage = company.stage
        if body.stage_key:
            stage = Stage.objects.filter(channel=channel, key=body.stage_key).first()
            if stage is None:
                raise NotFound("Stage not found.")
        runs = rule_service.evaluate_rules(
            company, body.trigger, stage=stage if body.trigger == RuleTrigger.STAGE_ENTERED else None
        )
        return Response(
            DevEvaluateResponse(runs=[RuleRunResponse.model_validate(run) for run in runs]).model_dump(mode="json")
        )


class DevRotateNowView(DevelopmentView):
    @extend_schema(
        tags=_TAGS,
        summary="Run the daily rotation scan now (development only)",
        request=None,
        responses={200: DevRotateResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        self.channel(channel_idx)
        return Response(DevRotateResponse(rotated=rotation_service.rotate_unresponsive()).model_dump())


class DevImportNowView(DevelopmentView):
    """The upload runs in this process — no worker, so no shared `LEADS_IMPORT_TMP_DIR` is needed."""

    parser_classes = [MultiPartParser]

    @extend_schema(
        tags=_TAGS,
        summary="Upload a CSV and import it now (development only)",
        request=UPLOAD_SCHEMA,
        responses={200: ImportBatchDetailResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        filename, content, size_bytes = read_upload(request)
        batch = import_service.create_batch(self.channel(channel_idx), filename, size_bytes, request.user.username)
        batch = import_service.run_content(batch, content)
        return Response(ImportBatchDetailResponse.model_validate(batch).model_dump(mode="json"))
