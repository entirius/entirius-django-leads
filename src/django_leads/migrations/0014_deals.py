# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.


import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("django_leads", "0013_tasks_resume_days"),
        ("django_regional", "0002_reset_pk_sequences"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="channel",
            name="default_currency",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="django_regional.currency",
            ),
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
                ],
                max_length=32,
            ),
        ),
        migrations.CreateModel(
            name="Deal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("modified_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=255)),
                ("stage_entered_at", models.DateTimeField()),
                ("value_one_off", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("value_recurring", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                (
                    "recurring_period",
                    models.CharField(choices=[("month", "Month"), ("year", "Year")], default="month", max_length=8),
                ),
                ("expected_close_date", models.DateField(blank=True, null=True)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("lost_reason", models.CharField(blank=True, default="", max_length=255)),
                ("attributes", models.JSONField(blank=True, default=dict)),
                ("created_by", models.CharField(default="system", max_length=128)),
                (
                    "channel",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="deals", to="django_leads.channel"
                    ),
                ),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="deals", to="django_leads.company"
                    ),
                ),
                (
                    "contact",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="deals",
                        to="django_leads.contact",
                    ),
                ),
                (
                    "currency",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="django_regional.currency",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddField(
            model_name="activity",
            name="deal",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="activities",
                to="django_leads.deal",
            ),
        ),
        migrations.CreateModel(
            name="DealStage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("modified_at", models.DateTimeField(auto_now=True)),
                ("key", models.SlugField(max_length=64)),
                ("label", models.CharField(max_length=128)),
                ("order", models.PositiveSmallIntegerField(default=0)),
                (
                    "kind",
                    models.CharField(
                        choices=[("open", "Open"), ("won", "Won"), ("lost", "Lost")], default="open", max_length=16
                    ),
                ),
                (
                    "channel",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="deal_stages",
                        to="django_leads.channel",
                    ),
                ),
            ],
            options={
                "ordering": ["order", "id"],
            },
        ),
        migrations.AddField(
            model_name="deal",
            name="stage",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, related_name="deals", to="django_leads.dealstage"
            ),
        ),
        migrations.AddConstraint(
            model_name="dealstage",
            constraint=models.UniqueConstraint(fields=("channel", "key"), name="leads_dealstage_channel_key_uniq"),
        ),
        migrations.AddIndex(
            model_name="deal",
            index=models.Index(fields=["channel", "stage"], name="leads_deal_channel_stage"),
        ),
        migrations.AddIndex(
            model_name="deal",
            index=models.Index(fields=["channel", "owner"], name="leads_deal_channel_owner"),
        ),
        migrations.AddIndex(
            model_name="deal",
            index=models.Index(fields=["company"], name="leads_deal_company"),
        ),
    ]
