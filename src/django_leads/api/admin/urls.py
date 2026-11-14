# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API URL routing — manual `path()` per Volkanos convention."""

from django.apps import apps
from django.urls import path

from django_leads.api.admin.views import activity_views as activity
from django_leads.api.admin.views import attribute_views as attribute
from django_leads.api.admin.views import channel_views as channel
from django_leads.api.admin.views import company_action_views as action
from django_leads.api.admin.views import company_views as company
from django_leads.api.admin.views import contact_views as contact
from django_leads.api.admin.views import deal_stage_views as deal_stage
from django_leads.api.admin.views import deal_views as deal
from django_leads.api.admin.views import import_views as imports
from django_leads.api.admin.views import lead_type_views as lead_type
from django_leads.api.admin.views import owner_views as owner
from django_leads.api.admin.views import profile_views as profile
from django_leads.api.admin.views import report_views as report
from django_leads.api.admin.views import rule_views as rule
from django_leads.api.admin.views import scoring_views as scoring
from django_leads.api.admin.views import stage_event_views as stage_event
from django_leads.api.admin.views import stage_views as stage
from django_leads.api.admin.views import test_views as dev

urlpatterns = [
    path("companies/", company.CompanyListView.as_view(), name="admin-leads-companies"),
    path("companies/assign/", owner.OwnerAssignView.as_view(), name="admin-leads-companies-assign"),
    path("companies/<int:pk>/", company.CompanyDetailView.as_view(), name="admin-leads-company"),
    path("companies/<int:pk>/transition/", company.CompanyTransitionView.as_view(), name="admin-leads-transition"),
    path("companies/<int:pk>/score/", scoring.CompanyScoreView.as_view(), name="admin-leads-company-score"),
    path("companies/<int:pk>/communicate/", action.CompanyCommunicateView.as_view(), name="admin-leads-communicate"),
    path("companies/<int:pk>/compose/", action.CompanyComposeView.as_view(), name="admin-leads-compose"),
    path(
        "companies/<int:pk>/request-audit/", action.CompanyRequestAuditView.as_view(), name="admin-leads-request-audit"
    ),
    path(
        "companies/<int:pk>/contacts/<int:contact_id>/mailable/",
        action.ContactMailableView.as_view(),
        name="admin-leads-contact-mailable",
    ),
    path("companies/<int:pk>/deals/", deal.CompanyDealListView.as_view(), name="admin-leads-company-deals"),
    path("deals/", deal.DealListView.as_view(), name="admin-leads-deals"),
    path("deals/board/", deal.DealBoardView.as_view(), name="admin-leads-deals-board"),
    path("deals/<int:pk>/", deal.DealDetailView.as_view(), name="admin-leads-deal"),
    path("deals/<int:pk>/transition/", deal.DealTransitionView.as_view(), name="admin-leads-deal-transition"),
    path("deals/<int:pk>/archive/", deal.DealArchiveView.as_view(), name="admin-leads-deal-archive"),
    path("deals/<int:pk>/restore/", deal.DealRestoreView.as_view(), name="admin-leads-deal-restore"),
    path("deals/<int:pk>/activities/", deal.DealActivityListView.as_view(), name="admin-leads-deal-activities"),
    path("deal-stages/", deal_stage.DealStageListView.as_view(), name="admin-leads-deal-stages"),
    path("deal-stages/<int:pk>/", deal_stage.DealStageDetailView.as_view(), name="admin-leads-deal-stage"),
    path("channel/", channel.ChannelSettingsView.as_view(), name="admin-leads-channel"),
    path("assignable-users/", owner.AssignableUserListView.as_view(), name="admin-leads-assignable-users"),
    path("contacts/", contact.ContactListView.as_view(), name="admin-leads-contacts"),
    path("contacts/<int:pk>/", contact.ContactDetailView.as_view(), name="admin-leads-contact"),
    path("stages/", stage.StageListView.as_view(), name="admin-leads-stages"),
    path("stages/<int:pk>/", stage.StageDetailView.as_view(), name="admin-leads-stage"),
    path("stage-events/", stage_event.StageEventListView.as_view(), name="admin-leads-stage-events"),
    path("stage-events/<str:event>/", stage_event.StageEventDetailView.as_view(), name="admin-leads-stage-event"),
    path("lead-types/", lead_type.LeadTypeListView.as_view(), name="admin-leads-lead-types"),
    path("lead-types/<int:pk>/", lead_type.LeadTypeDetailView.as_view(), name="admin-leads-lead-type"),
    path("attributes/", attribute.AttributeListView.as_view(), name="admin-leads-attributes"),
    path("attributes/<int:pk>/", attribute.AttributeDetailView.as_view(), name="admin-leads-attribute"),
    path("fields/", attribute.FieldListView.as_view(), name="admin-leads-fields"),
    path("scoring-profiles/", scoring.ScoringProfileListView.as_view(), name="admin-leads-scoring-profiles"),
    path(
        "scoring-profiles/<int:pk>/",
        scoring.ScoringProfileDetailView.as_view(),
        name="admin-leads-scoring-profile",
    ),
    path("activities/", activity.ActivityListView.as_view(), name="admin-leads-activities"),
    path("imports/", imports.ImportListView.as_view(), name="admin-leads-imports"),
    path("imports/<int:pk>/", imports.ImportDetailView.as_view(), name="admin-leads-import"),
    path("reports/funnel/", report.FunnelView.as_view(), name="admin-leads-report-funnel"),
    path("rules/", rule.RuleListView.as_view(), name="admin-leads-rules"),
    path("rules/<int:pk>/", rule.RuleDetailView.as_view(), name="admin-leads-rule"),
    path("rule-runs/", rule.RuleRunListView.as_view(), name="admin-leads-rule-runs"),
    path("analysis-profiles/", profile.AnalysisProfileListView.as_view(), name="admin-leads-analysis-profiles"),
    path(
        "analysis-profiles/<int:pk>/", profile.AnalysisProfileDetailView.as_view(), name="admin-leads-analysis-profile"
    ),
    path("recipient-profiles/", profile.RecipientProfileListView.as_view(), name="admin-leads-recipient-profiles"),
    path(
        "recipient-profiles/<int:pk>/",
        profile.RecipientProfileDetailView.as_view(),
        name="admin-leads-recipient-profile",
    ),
]

# Won seam: absent without django_accounts, and its module is never imported then (L-15).
if apps.is_installed("django_accounts"):
    from django_leads.api.admin.views import customer_link_views as customer

    urlpatterns.append(
        path(
            "companies/<int:pk>/create-customer/",
            customer.CompanyCreateCustomerView.as_view(),
            name="admin-leads-create-customer",
        )
    )

if dev.is_development():
    urlpatterns += [
        path("test/import-now/", dev.DevImportNowView.as_view(), name="admin-leads-test-import-now"),
        path("test/evaluate/", dev.DevEvaluateView.as_view(), name="admin-leads-test-evaluate"),
        path("test/rotate-now/", dev.DevRotateNowView.as_view(), name="admin-leads-test-rotate-now"),
        path("test/anonymise-now/", dev.DevAnonymiseNowView.as_view(), name="admin-leads-test-anonymise-now"),
        path("test/retry-analyses/", dev.DevRetryAnalysesView.as_view(), name="admin-leads-test-retry-analyses"),
    ]
