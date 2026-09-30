# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models

UNKNOWN = "UNKNOWN"


def create_used_lead_types(apps, schema_editor):
    """Every code a company of a channel already carries becomes a lead type row of that channel; a blank code (left
    by the pre-0007 import) becomes `UNKNOWN`. The reverse keeps codes as they are — safe only for codes ≤ 16 chars."""
    Company = apps.get_model("django_leads", "Company")
    LeadType = apps.get_model("django_leads", "LeadType")
    Company.objects.filter(lead_type="").update(lead_type=UNKNOWN)
    used = Company.objects.exclude(lead_type__in=["", UNKNOWN]).values_list("channel_id", "lead_type").distinct()
    for channel_id, code in sorted(used):
        order = LeadType.objects.filter(channel_id=channel_id).count() * 10
        LeadType.objects.get_or_create(
            channel_id=channel_id, code=code, defaults={"label": code.capitalize(), "order": order}
        )


class Migration(migrations.Migration):
    dependencies = [
        ("django_leads", "0006_erased_address"),
    ]

    operations = [
        migrations.RenameField(model_name="company", old_name="company_type", new_name="lead_type"),
        migrations.AlterField(
            model_name="company",
            name="lead_type",
            field=models.CharField(default=UNKNOWN, max_length=32),
        ),
        migrations.CreateModel(
            name="LeadType",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("modified_at", models.DateTimeField(auto_now=True)),
                (
                    "code",
                    models.CharField(
                        max_length=32,
                        validators=[
                            django.core.validators.RegexValidator("^[A-Z0-9_]+$", "Upper-case code, e.g. RETAILER.")
                        ],
                    ),
                ),
                ("label", models.CharField(max_length=128)),
                ("order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "channel",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lead_types",
                        to="django_leads.channel",
                    ),
                ),
            ],
            options={
                "ordering": ["order", "code"],
                "constraints": [
                    models.UniqueConstraint(fields=("channel", "code"), name="leads_lead_type_channel_code_uniq")
                ],
            },
        ),
        migrations.RunPython(create_used_lead_types, migrations.RunPython.noop),
    ]
