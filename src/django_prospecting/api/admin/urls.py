# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API URL routing — manual `path()` per Volkanos convention. URL names start with `prospecting-`."""

from django.urls import path

from django_prospecting.api.admin.views.company_views import CompanyListView
from django_prospecting.api.admin.views.people_views import (
    CandidateAcceptView,
    CandidateListView,
    CandidateRejectView,
    LimitsView,
    SearchListView,
)

urlpatterns = [
    path("companies/", CompanyListView.as_view(), name="prospecting-companies-list"),
    path("people/searches/", SearchListView.as_view(), name="prospecting-searches-list"),
    path("people/candidates/", CandidateListView.as_view(), name="prospecting-candidates-list"),
    path("people/candidates/<int:pk>/accept/", CandidateAcceptView.as_view(), name="prospecting-candidates-accept"),
    path("people/candidates/<int:pk>/reject/", CandidateRejectView.as_view(), name="prospecting-candidates-reject"),
    path("people/limits/", LimitsView.as_view(), name="prospecting-limits"),
]
