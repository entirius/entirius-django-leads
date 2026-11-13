# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("django_leads", "0007_lead_types"),
    ]

    operations = [
        migrations.CreateModel(
            name="StageEventRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("modified_at", models.DateTimeField(auto_now=True)),
                (
                    "event",
                    models.CharField(
                        choices=[
                            ("first_sent", "First mail sent"),
                            ("reply", "Reply"),
                            ("reply:interested", "Reply: interested"),
                            ("reply:not_now", "Reply: not now"),
                            ("reply:not_interested", "Reply: not interested"),
                            ("reply:out_of_office", "Reply: out of office"),
                            ("reply:wrong_person", "Reply: wrong person"),
                            ("reply:unsubscribe", "Reply: unsubscribe"),
                            ("reply:unclear", "Reply: unclear"),
                            ("sequence_finished", "Sequence finished"),
                        ],
                        max_length=32,
                    ),
                ),
                ("forward_only", models.BooleanField(default=True)),
                (
                    "channel",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="stage_events",
                        to="django_leads.channel",
                    ),
                ),
                (
                    "target_stage",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="django_leads.stage",
                    ),
                ),
            ],
            options={
                "ordering": ["event"],
                "constraints": [
                    models.UniqueConstraint(fields=("channel", "event"), name="leads_stageevent_channel_event_uniq")
                ],
            },
        ),
        migrations.AddField(
            model_name="company",
            name="origin",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="contact",
            name="wrong_person_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="importbatch",
            name="run_rules",
            field=models.BooleanField(default=False),
        ),
    ]
