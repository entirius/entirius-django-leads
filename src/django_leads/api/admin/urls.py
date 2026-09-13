# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API URL routing — manual `path()` per Volkanos convention."""

from django.urls import path

from django_leads.api.admin.views import activity_views as activity
from django_leads.api.admin.views import company_views as company
from django_leads.api.admin.views import contact_views as contact
from django_leads.api.admin.views import import_views as imports
from django_leads.api.admin.views import stage_views as stage

urlpatterns = [
    path("companies/", company.CompanyListView.as_view(), name="admin-leads-companies"),
    path("companies/<int:pk>/", company.CompanyDetailView.as_view(), name="admin-leads-company"),
    path("companies/<int:pk>/transition/", company.CompanyTransitionView.as_view(), name="admin-leads-transition"),
    path("contacts/", contact.ContactListView.as_view(), name="admin-leads-contacts"),
    path("contacts/<int:pk>/", contact.ContactDetailView.as_view(), name="admin-leads-contact"),
    path("stages/", stage.StageListView.as_view(), name="admin-leads-stages"),
    path("stages/<int:pk>/", stage.StageDetailView.as_view(), name="admin-leads-stage"),
    path("activities/", activity.ActivityListView.as_view(), name="admin-leads-activities"),
    path("imports/", imports.ImportListView.as_view(), name="admin-leads-imports"),
    path("imports/<int:pk>/", imports.ImportDetailView.as_view(), name="admin-leads-import"),
]
