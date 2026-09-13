# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — CSV imports: list, upload (queued), detail with the report."""

from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, PAGE_PARAMETERS, AdminView
from django_leads.models import ImportBatch
from django_leads.schemas.responses import ImportBatchDetailResponse, ImportBatchListResponse, ImportBatchResponse
from django_leads.services import import_service
from django_leads.tasks import enqueue_import

_TAGS = ["Leads imports"]
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
UPLOAD_SCHEMA = {
    "multipart/form-data": {"type": "object", "properties": {"file": {"type": "string", "format": "binary"}}}
}


def read_upload(request: Request) -> tuple[str, str, int]:
    upload = request.FILES.get("file")
    if upload is None:
        raise ValidationError({"file": ["a CSV file is required"]})
    if upload.size > MAX_UPLOAD_BYTES:
        raise ValidationError({"file": ["the file is too large"]})
    try:
        return upload.name, upload.read().decode("utf-8-sig"), upload.size
    except UnicodeDecodeError:
        raise ValidationError({"file": ["the file must be UTF-8 text"]}) from None


class ImportListView(AdminView):
    parser_classes = [MultiPartParser]

    @extend_schema(
        tags=_TAGS,
        operation_id="leads_imports_list",
        summary="Import batches of the channel, newest first",
        parameters=PAGE_PARAMETERS,
        responses={200: ImportBatchListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        batches = ImportBatch.objects.filter(channel=self.channel(channel_idx)).defer("report")
        return self.paginated(request, batches, ImportBatchResponse.model_validate)

    @extend_schema(
        tags=_TAGS,
        summary="Upload a CSV; the import runs on the leads queue",
        request=UPLOAD_SCHEMA,
        responses={202: ImportBatchResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        filename, content, size_bytes = read_upload(request)
        batch = import_service.create_batch(self.channel(channel_idx), filename, size_bytes, request.user.username)
        transaction.on_commit(lambda: enqueue_import(batch.pk, content))
        return Response(ImportBatchResponse.model_validate(batch).model_dump(mode="json"), status=202)


class ImportDetailView(AdminView):
    @extend_schema(
        tags=_TAGS,
        summary="One import batch with its report",
        responses={200: ImportBatchDetailResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        batch = self.get_in(ImportBatch.objects.filter(channel=self.channel(channel_idx)), pk, "Import batch")
        return Response(ImportBatchDetailResponse.model_validate(batch).model_dump(mode="json"))
