# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("django_leads", "0012_owner"),
    ]

    operations = [
        migrations.AddField(
            model_name="channel",
            name="resume_days",
            field=models.JSONField(blank=True, default=dict),
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
                ],
                max_length=32,
            ),
        ),
    ]
