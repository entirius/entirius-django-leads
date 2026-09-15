# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from django.contrib import admin

from django_leads.models import (
    Activity,
    AnalysisProfile,
    Channel,
    Company,
    Contact,
    ImportBatch,
    RecipientPickProfile,
    RuleRun,
    Stage,
    StageRule,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


class StageInline(admin.TabularInline):
    model = Stage
    extra = 0
    fields = ("order", "key", "label", "kind", "is_terminal", "on_reply")


@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):
    list_display = ("idx", "name", "default_language", "retention_days")
    search_fields = ("idx", "name")
    filter_horizontal = ("languages",)
    inlines = [StageInline]


class ContactInline(admin.TabularInline):
    model = Contact
    extra = 0
    fields = ("email", "first_name", "last_name", "job_title", "is_primary", "legal_basis", "source")
    readonly_fields = ("email", "source", "legal_basis")


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    """Stage changes go through the API transition (timeline + signal) — read-only here, like hooks."""

    list_display = ("name", "domain", "channel", "stage", "source", "do_not_contact", "last_activity_at")
    list_filter = ("channel", "stage", "source", "do_not_contact")
    search_fields = ("name", "domain")
    readonly_fields = ("domain", "stage", "stage_entered_at", "hooks", "customer_uid", "last_activity_at")
    inlines = [ContactInline]

    def has_add_permission(self, request) -> bool:
        """`stage` is read-only here, so an admin form could never save a new company."""
        return False


@admin.register(ImportBatch)
class ImportBatchAdmin(ReadOnlyAdmin):
    list_display = ("filename", "channel", "status", "created_count", "matched_count", "skipped_count", "created_at")
    list_filter = ("channel", "status")


@admin.register(Activity)
class ActivityAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "company", "kind", "message", "actor")
    list_filter = ("kind",)
    search_fields = ("company__name", "company__domain", "message")


@admin.register(StageRule)
class StageRuleAdmin(admin.ModelAdmin):
    list_display = ("channel", "order", "trigger", "stage", "action", "template_key", "contact_strategy", "is_active")
    list_filter = ("channel", "trigger", "action", "is_active")


@admin.register(RuleRun)
class RuleRunAdmin(ReadOnlyAdmin):
    list_display = ("fired_at", "rule", "company", "outcome", "detail")
    list_filter = ("outcome",)
    search_fields = ("company__name", "company__domain")


@admin.register(AnalysisProfile)
class AnalysisProfileAdmin(admin.ModelAdmin):
    """Prompt profiles are edited here and through the API only — never in the CMS."""

    list_display = ("channel", "key", "model", "is_active")


@admin.register(RecipientPickProfile)
class RecipientPickProfileAdmin(admin.ModelAdmin):
    list_display = ("channel", "key", "model")
