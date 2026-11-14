# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import django.contrib.postgres.indexes
import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("django_leads", "0010_remove_stage_on_reply"),
    ]

    operations = [
        migrations.CreateModel(
            name="AttributeDefinition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("modified_at", models.DateTimeField(auto_now=True)),
                (
                    "entity",
                    models.CharField(
                        choices=[("company", "Company"), ("contact", "Contact"), ("deal", "Deal")], max_length=16
                    ),
                ),
                (
                    "key",
                    models.CharField(
                        max_length=48,
                        validators=[
                            django.core.validators.RegexValidator(
                                "^[a-z][a-z0-9_]*$", "Lower-case slug: letters, digits, underscore."
                            )
                        ],
                    ),
                ),
                ("label", models.CharField(max_length=128)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("text", "Text"),
                            ("number", "Number"),
                            ("bool", "Yes / no"),
                            ("date", "Date"),
                            ("select", "Select"),
                            ("multiselect", "Multi-select"),
                            ("url", "URL"),
                        ],
                        max_length=16,
                    ),
                ),
                ("options", models.JSONField(blank=True, default=list)),
                ("required", models.BooleanField(default=False)),
                ("order", models.PositiveSmallIntegerField(default=0)),
                ("show_in_list", models.BooleanField(default=False)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "ordering": ["entity", "order", "key"],
            },
        ),
        migrations.CreateModel(
            name="ScoringProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("modified_at", models.DateTimeField(auto_now=True)),
                ("key", models.CharField(default="leads.scoring", max_length=64)),
                ("thresholds", models.JSONField(default=dict)),
                ("rules", models.JSONField(blank=True, default=list)),
                ("disqualifiers", models.JSONField(blank=True, default=list)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "ordering": ["key"],
            },
        ),
        migrations.AddField(
            model_name="company",
            name="attributes",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="company",
            name="score_points",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="company",
            name="score_segment",
            field=models.CharField(blank=True, default="", max_length=2),
        ),
        migrations.AddField(
            model_name="company",
            name="scored_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="contact",
            name="attributes",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="stagerule",
            name="conditions",
            field=models.JSONField(blank=True, default=list),
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
                ],
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="stagerule",
            name="action",
            field=models.CharField(
                choices=[("request_audit", "Request audit"), ("communicate", "Communicate"), ("score", "Score")],
                default="communicate",
                max_length=16,
            ),
        ),
        migrations.AddIndex(
            model_name="company",
            index=django.contrib.postgres.indexes.GinIndex(fields=["attributes"], name="leads_company_attributes_gin"),
        ),
        migrations.AddField(
            model_name="attributedefinition",
            name="channel",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="attribute_definitions",
                to="django_leads.channel",
            ),
        ),
        migrations.AddField(
            model_name="scoringprofile",
            name="channel",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name="scoring_profiles", to="django_leads.channel"
            ),
        ),
        migrations.AddConstraint(
            model_name="attributedefinition",
            constraint=models.UniqueConstraint(
                fields=("channel", "entity", "key"), name="leads_attribute_channel_entity_key_uniq"
            ),
        ),
        migrations.AddConstraint(
            model_name="scoringprofile",
            constraint=models.UniqueConstraint(
                fields=("channel", "key"), name="leads_scoring_profile_channel_key_uniq"
            ),
        ),
    ]
