# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — imports: list, preview of the column mapping, upload (queued), detail with the report."""

from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, PAGE_PARAMETERS, AdminView, parse
from django_leads.api.admin.views.owner_views import owner_or_400
from django_leads.models import ImportBatch
from django_leads.schemas.requests import UploadOptions
from django_leads.schemas.responses import (
    ImportBatchDetailResponse,
    ImportBatchListResponse,
    ImportBatchResponse,
    ImportPreviewResponse,
)
from django_leads.services import import_mapping_service, import_service
from django_leads.services.import_file_service import ImportFileError
from django_leads.tasks import enqueue_import

_TAGS = ["Leads Imports"]
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
UPLOAD_SCHEMA = {
    "multipart/form-data": {
        "type": "object",
        "properties": {
            "file": {"type": "string", "format": "binary"},
            "run_rules": {
                "type": "boolean",
                "default": False,
                "description": "Created companies enter their first stage with its rules (one evaluation each).",
            },
            "owner_id": {
                "type": "integer",
                "description": "Owner of created companies and of matched ones without an owner; absent = none.",
            },
            "mapping": {
                "type": "string",
                "description": 'JSON object {"<file column>": "<field key>" | "ignore"} (columns left out are '
                "ignored); absent = the exact-name import.",
            },
        },
        "required": ["file"],
    }
}
PREVIEW_SCHEMA = {
    "multipart/form-data": {
        "type": "object",
        "properties": {
            "file": {"type": "string", "format": "binary", "description": ".csv or .xlsx (first sheet)."},
            "mapping": {
                "type": "string",
                "description": 'JSON object {"<file column>": "<field key>" | "ignore"}: the user\'s choices; '
                "columns left out get the proposal.",
            },
        },
        "required": ["file"],
    }
}


def read_bytes(request: Request) -> tuple[str, bytes, int]:
    upload = request.FILES.get("file")
    if upload is None:
        raise ValidationError({"file": ["a CSV or XLSX file is required"]}, code="required")
    if upload.size > MAX_UPLOAD_BYTES:
        raise ValidationError({"file": ["the file is too large"]}, code="too_large")
    return upload.name, upload.read(), upload.size


def prepared_upload(request: Request, channel, mapping: str | None) -> tuple[str, int, import_mapping_service.Prepared]:
    """The upload as the import's CSV, or the v2 400 of the file or the mapping."""
    filename, data, size_bytes = read_bytes(request)
    try:
        return filename, size_bytes, import_mapping_service.prepare_import(channel, filename, data, mapping)
    except ImportFileError as error:
        raise ValidationError({"file": [error.message]}, code=error.code) from None
    except import_mapping_service.MappingError as error:
        raise ValidationError({error.field: [error.message]}, code=error.code) from None


class ImportListView(AdminView):
    access_area = "leads.companies"
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
        return self.paginated(request, batches, ImportBatchResponse.of)

    @extend_schema(
        tags=_TAGS,
        summary="Upload a CSV or XLSX; the import runs on the leads queue",
        description="Without `mapping` the exact column names are read (`company_name`, `domain`, …); with it the "
        "file is read as `imports/preview/` reads it and each column goes to its mapped field.",
        request=UPLOAD_SCHEMA,
        responses={202: ImportBatchResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        options = parse(UploadOptions, request.data.dict())
        channel = self.channel(channel_idx)
        owner = owner_or_400(options.owner_id)
        filename, size_bytes, prepared = prepared_upload(request, channel, options.mapping)
        batch = import_service.create_batch(
            channel,
            filename,
            size_bytes,
            request.user.username,
            run_rules=options.run_rules,
            owner=owner,
            column_mapping=prepared.column_mapping,
            ignored_columns=prepared.ignored_columns,
        )
        import_service.store_upload(batch.pk, prepared.content)
        transaction.on_commit(lambda: enqueue_import(batch.pk))
        return Response(ImportBatchResponse.of(batch).model_dump(mode="json"), status=202)


class ImportDetailView(AdminView):
    access_area = "leads.companies"

    @extend_schema(
        tags=_TAGS,
        summary="One import batch with its report",
        responses={200: ImportBatchDetailResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        batch = self.get_in(ImportBatch.objects.filter(channel=self.channel(channel_idx)), pk, "Import batch")
        return Response(ImportBatchDetailResponse.of(batch).model_dump(mode="json"))


class ImportPreviewView(AdminView):
    access_area = "leads.companies"
    parser_classes = [MultiPartParser]

    @extend_schema(
        tags=_TAGS,
        operation_id="leads_imports_preview",
        summary="Propose a column mapping for a CSV or XLSX and show the first rows after it",
        description="Nothing is stored: the import sends the file again with the confirmed `mapping`.",
        request=PREVIEW_SCHEMA,
        responses={200: ImportPreviewResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        channel = self.channel(channel_idx)
        filename, data, _ = read_bytes(request)
        mapping = request.data.get("mapping")
        try:
            preview = import_mapping_service.preview(channel, filename, data, mapping)
        except ImportFileError as error:
            raise ValidationError({"file": [error.message]}, code=error.code) from None
        except import_mapping_service.MappingError as error:
            raise ValidationError({error.field: [error.message]}, code=error.code) from None
        return Response(ImportPreviewResponse.model_validate(preview).model_dump(mode="json"))
