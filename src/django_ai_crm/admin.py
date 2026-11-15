# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from django.contrib import admin

from django_ai_crm.models import Proposal


@admin.register(Proposal)
class ProposalAdmin(admin.ModelAdmin):
    list_display = ("id", "tool", "user", "channel_idx", "source", "status", "created_at")
    list_filter = ("status", "tool", "source")
    readonly_fields = [field.name for field in Proposal._meta.fields]

    def has_add_permission(self, request):
        return False
