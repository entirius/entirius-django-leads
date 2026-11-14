# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("django_leads", "0015_import_batch_report_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="deal",
            name="archived_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
