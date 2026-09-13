# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — stage rules (CRUD) and their runs (read-only)."""

from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_leads.api.admin.views._base import ERROR_RESPONSES, PAGE_PARAMETERS, AdminView, parse
from django_leads.models import StageRule
from django_leads.schemas.requests import RuleRequest, RuleRunListQuery, RuleUpdateRequest
from django_leads.schemas.responses import RuleListResponse, RuleResponse, RuleRunListResponse, RuleRunResponse
from django_leads.services import config_service

_TAGS = ["Leads rules"]


def dump(rule: StageRule) -> dict:
    return RuleResponse.model_validate(rule).model_dump(mode="json")


def save(rule: StageRule, fields: dict) -> StageRule:
    try:
        return config_service.save_rule(rule, fields)
    except ValueError as error:
        raise ValidationError({"detail": [str(error)]}) from None
    except DjangoValidationError as error:
        raise ValidationError(error.message_dict) from None


class RuleListView(AdminView):
    @extend_schema(
        tags=_TAGS,
        operation_id="leads_rules_list",
        summary="Rules of the channel in evaluation order",
        responses={200: RuleListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        rules = config_service.list_rules(self.channel(channel_idx))
        return Response({"results": [dump(rule) for rule in rules]})

    @extend_schema(
        tags=_TAGS, summary="Add a rule", request=RuleRequest, responses={201: RuleResponse, **ERROR_RESPONSES}
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        fields = parse(RuleRequest, request.data).model_dump(mode="json")
        rule = save(StageRule(channel=self.channel(channel_idx)), fields)
        return Response(dump(rule), status=201)


class RuleDetailView(AdminView):
    def rule(self, channel_idx: str, pk: int) -> StageRule:
        return self.get_in(config_service.list_rules(self.channel(channel_idx)), pk, "Rule")

    @extend_schema(tags=_TAGS, summary="One rule", responses={200: RuleResponse, **ERROR_RESPONSES})
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        return Response(dump(self.rule(channel_idx, pk)))

    @extend_schema(
        tags=_TAGS, summary="Update a rule", request=RuleUpdateRequest, responses={200: RuleResponse, **ERROR_RESPONSES}
    )
    def patch(self, request: Request, channel_idx: str, pk: int) -> Response:
        updates = parse(RuleUpdateRequest, request.data).model_dump(mode="json", exclude_unset=True)
        if any(value is None for field, value in updates.items() if field != "stage_id"):
            raise ValidationError({"detail": ["null is only allowed for stage_id"]})
        return Response(dump(save(self.rule(channel_idx, pk), updates)))

    @extend_schema(tags=_TAGS, summary="Delete a rule and its runs", responses={204: None, **ERROR_RESPONSES})
    def delete(self, request: Request, channel_idx: str, pk: int) -> Response:
        self.rule(channel_idx, pk).delete()
        return Response(status=204)


class RuleRunListView(AdminView):
    @extend_schema(
        tags=_TAGS,
        operation_id="leads_rule_runs_list",
        summary="Rule runs of the channel, newest first",
        parameters=[OpenApiParameter("company", int, description="Company id."), *PAGE_PARAMETERS],
        responses={200: RuleRunListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(RuleRunListQuery, request.query_params.dict())
        runs = config_service.list_rule_runs(self.channel(channel_idx), company_id=query.company)
        return self.paginated(request, runs, RuleRunResponse.model_validate)
