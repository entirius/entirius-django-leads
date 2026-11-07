# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API URL routing — manual `path()` per Volkanos convention."""

from django.apps import apps
from django.urls import path

from django_leads.api.admin.views import activity_views as activity
from django_leads.api.admin.views import company_action_views as action
from django_leads.api.admin.views import company_views as company
from django_leads.api.admin.views import compliance_views as compliance
from django_leads.api.admin.views import contact_views as contact
from django_leads.api.admin.views import import_views as imports
from django_leads.api.admin.views import lead_type_views as lead_type
from django_leads.api.admin.views import pilot_views as pilot
from django_leads.api.admin.views import profile_views as profile
from django_leads.api.admin.views import reply_route_views as reply_route
from django_leads.api.admin.views import report_views as report
from django_leads.api.admin.views import rule_views as rule
from django_leads.api.admin.views import scoring_views as scoring
from django_leads.api.admin.views import stage_views as stage
from django_leads.api.admin.views import test_views as dev

urlpatterns = [
    path("companies/", company.CompanyListView.as_view(), name="admin-leads-companies"),
    path("companies/<int:pk>/", company.CompanyDetailView.as_view(), name="admin-leads-company"),
    path("companies/<int:pk>/transition/", company.CompanyTransitionView.as_view(), name="admin-leads-transition"),
    path("companies/<int:pk>/communicate/", action.CompanyCommunicateView.as_view(), name="admin-leads-communicate"),
    path(
        "companies/<int:pk>/request-audit/", action.CompanyRequestAuditView.as_view(), name="admin-leads-request-audit"
    ),
    path("companies/<int:pk>/score/", scoring.CompanyScoreView.as_view(), name="admin-leads-score"),
    path("contacts/", contact.ContactListView.as_view(), name="admin-leads-contacts"),
    path("contacts/<int:pk>/", contact.ContactDetailView.as_view(), name="admin-leads-contact"),
    path("contacts/<int:pk>/send-check/", compliance.SendCheckView.as_view(), name="admin-leads-send-check"),
    path("contacts/<int:pk>/verify/", compliance.VerifyContactView.as_view(), name="admin-leads-verify-contact"),
    path("reports/verification/", compliance.VerificationReportView.as_view(), name="admin-leads-verification"),
    path("companies/bulk-sender-route/", compliance.BulkSenderRouteView.as_view(), name="admin-leads-bulk-route"),
    path("settings/", compliance.ChannelSettingsView.as_view(), name="admin-leads-channel-settings"),
    path("do-not-contact/", compliance.DoNotContactView.as_view(), name="admin-leads-do-not-contact"),
    path("reply-routes/", reply_route.ReplyRouteListView.as_view(), name="admin-leads-reply-routes"),
    path("reply-routes/<str:intent>/", reply_route.ReplyRouteDetailView.as_view(), name="admin-leads-reply-route"),
    path("stages/", stage.StageListView.as_view(), name="admin-leads-stages"),
    path("stages/<int:pk>/", stage.StageDetailView.as_view(), name="admin-leads-stage"),
    path("queue/", report.QueueView.as_view(), name="admin-leads-queue"),
    path("reports/funnel/", report.FunnelReportView.as_view(), name="admin-leads-funnel"),
    path("pilots/", pilot.PilotListView.as_view(), name="admin-leads-pilots"),
    path("pilots/report/", pilot.PilotReportView.as_view(), name="admin-leads-pilot-report"),
    path("pilots/<int:pk>/", pilot.PilotDetailView.as_view(), name="admin-leads-pilot"),
    path("lead-types/", lead_type.LeadTypeListView.as_view(), name="admin-leads-lead-types"),
    path("lead-types/<int:pk>/", lead_type.LeadTypeDetailView.as_view(), name="admin-leads-lead-type"),
    path("activities/", activity.ActivityListView.as_view(), name="admin-leads-activities"),
    path("imports/", imports.ImportListView.as_view(), name="admin-leads-imports"),
    path("imports/<int:pk>/", imports.ImportDetailView.as_view(), name="admin-leads-import"),
    path("rules/", rule.RuleListView.as_view(), name="admin-leads-rules"),
    path("rules/<int:pk>/", rule.RuleDetailView.as_view(), name="admin-leads-rule"),
    path("rule-runs/", rule.RuleRunListView.as_view(), name="admin-leads-rule-runs"),
    path("analysis-profiles/", profile.AnalysisProfileListView.as_view(), name="admin-leads-analysis-profiles"),
    path(
        "analysis-profiles/<int:pk>/", profile.AnalysisProfileDetailView.as_view(), name="admin-leads-analysis-profile"
    ),
    path("scoring-profiles/", scoring.ScoringProfileListView.as_view(), name="admin-leads-scoring-profiles"),
    path("scoring-profiles/<int:pk>/", scoring.ScoringProfileDetailView.as_view(), name="admin-leads-scoring-profile"),
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
        path("test/resume-now/", dev.DevResumeNowView.as_view(), name="admin-leads-test-resume-now"),
        path("test/digest-now/", dev.DevDigestNowView.as_view(), name="admin-leads-test-digest-now"),
        path("test/retry-analyses/", dev.DevRetryAnalysesView.as_view(), name="admin-leads-test-retry-analyses"),
    ]
