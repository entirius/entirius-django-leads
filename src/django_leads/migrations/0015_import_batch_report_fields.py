# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("django_leads", "0014_deals"),
    ]

    operations = [
        migrations.AddField(
            model_name="importbatch",
            name="automation_started_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="importbatch",
            name="ignored_columns",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
