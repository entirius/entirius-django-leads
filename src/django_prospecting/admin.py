# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from django.contrib import admin

from django_prospecting.models import Candidate, CandidateCheck, CompanyProspect, PeopleSearch


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(PeopleSearch)
class PeopleSearchAdmin(ReadOnlyAdmin):
    list_display = ("company_name", "channel_idx", "reason", "status", "attempts", "created_at")
    list_filter = ("status", "reason")


class CheckInline(admin.TabularInline):
    model = CandidateCheck
    extra = 0
    can_delete = False


@admin.register(Candidate)
class CandidateAdmin(ReadOnlyAdmin):
    list_display = ("first_name", "last_name", "job_title", "subject_ref", "status", "email")
    list_filter = ("status",)
    inlines = [CheckInline]


@admin.register(CompanyProspect)
class CompanyProspectAdmin(admin.ModelAdmin):
    list_display = ("name", "domain", "channel_idx", "created_at")
