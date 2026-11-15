# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API URL routing — manual `path()` per Volkanos convention. URL names start with `ai-crm-`."""

from django.urls import path

from django_ai_crm.api.admin.views.agent_views import (
    ChatView,
    PlanView,
    ProposalListView,
    ToolCallView,
    ToolListView,
)
from django_ai_crm.api.admin.views.mcp_views import McpView

urlpatterns = [
    path("plan/", PlanView.as_view(), name="ai-crm-plan"),
    path("tools/", ToolListView.as_view(), name="ai-crm-tools"),
    path("tools/<str:name>/", ToolCallView.as_view(), name="ai-crm-tool-call"),
    path("proposals/", ProposalListView.as_view(), name="ai-crm-proposals"),
    path("chat/", ChatView.as_view(), name="ai-crm-chat"),
    path("mcp/", McpView.as_view(), name="ai-crm-mcp"),
]
