# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("django_leads", "0017_history_journal"),
    ]

    operations = [
        migrations.AddField(
            model_name="channel",
            name="potential_settings",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="company",
            name="potential",
            field=models.CharField(
                blank=True, choices=[("green", "Green"), ("orange", "Orange"), ("red", "Red")], default="", max_length=8
            ),
        ),
        migrations.AddField(
            model_name="company",
            name="potential_changed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="company",
            name="potential_comment",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="company",
            name="potential_manual",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="company",
            name="potential_reason",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="company",
            name="potential_return_on",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="activity",
            name="kind",
            field=models.CharField(
                choices=[
                    ("import", "Import"),
                    ("form", "Form"),
                    ("form_import_failed", "Form import failed"),
                    ("stage", "Stage"),
                    ("rule", "Rule"),
                    ("draft", "Draft"),
                    ("sent", "Sent"),
                    ("reply", "Reply"),
                    ("bounce", "Bounce"),
                    ("optout", "Opt-out"),
                    ("note", "Note"),
                    ("anonymised", "Anonymised"),
                    ("blocked", "Blocked"),
                    ("skipped", "Skipped"),
                    ("rotation", "Rotation"),
                    ("rotation_failed", "Rotation failed"),
                    ("rotation_gave_up", "Rotation gave up"),
                    ("legal_basis", "Legal basis"),
                    ("legal_basis_conflict", "Legal basis conflict"),
                    ("intel", "Intel"),
                    ("intel_empty", "Intel empty"),
                    ("score", "Score"),
                    ("task", "Task"),
                    ("deal", "Deal"),
                    ("settings", "Settings"),
                    ("potential", "Potential"),
                ],
                max_length=32,
            ),
        ),
        migrations.AddIndex(
            model_name="company",
            index=models.Index(fields=["channel", "potential"], name="leads_company_channel_potent"),
        ),
    ]
