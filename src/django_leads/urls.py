# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Root URL config for django_leads — mounts the Admin API v2 namespace."""

from django.urls import include, path

from django_leads.api.admin.views import gdpr_views as gdpr

urlpatterns = [
    path("api/leads/v2/admin/gdpr/export/", gdpr.GdprExportView.as_view(), name="admin-leads-gdpr-export"),
    path("api/leads/v2/admin/gdpr/erase/", gdpr.GdprEraseView.as_view(), name="admin-leads-gdpr-erase"),
    path("api/leads/v2/admin/<str:channel_idx>/", include("django_leads.api.admin.urls")),
]
