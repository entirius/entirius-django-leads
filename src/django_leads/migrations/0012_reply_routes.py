# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("django_leads", "0011_company_origin"),
    ]

    operations = [
        migrations.AddField(
            model_name="contact",
            name="wrong_person_at",
            field=models.DateTimeField(
                blank=True,
                help_text="A reply said this is the wrong person (T4): no more outreach to this contact.",
                null=True,
            ),
        ),
        migrations.CreateModel(
            name="ReplyIntentRoute",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("modified_at", models.DateTimeField(auto_now=True)),
                (
                    "intent",
                    models.CharField(
                        choices=[
                            ("interested", "Interested"),
                            ("not_now", "Not now"),
                            ("not_interested", "Not interested"),
                            ("out_of_office", "Out of office"),
                            ("wrong_person", "Wrong person"),
                            ("unsubscribe", "Unsubscribe"),
                            ("unclear", "Unclear"),
                        ],
                        max_length=24,
                    ),
                ),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("reply_stage", "Move to the on-reply stage"),
                            ("stage", "Move to a stage"),
                            ("none", "Keep the stage"),
                        ],
                        default="reply_stage",
                        max_length=16,
                    ),
                ),
                (
                    "flag_contact",
                    models.BooleanField(
                        default=False,
                        help_text="Mark the replying contact as the wrong person: no more outreach to them.",
                    ),
                ),
                (
                    "channel",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reply_routes",
                        to="django_leads.channel",
                    ),
                ),
                (
                    "stage",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reply_routes",
                        to="django_leads.stage",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("channel", "intent"), name="leads_replyroute_channel_intent")
                ],
            },
        ),
    ]
