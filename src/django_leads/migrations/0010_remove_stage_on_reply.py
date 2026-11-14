# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("django_leads", "0009_on_reply_to_stage_events"),
    ]

    operations = [
        migrations.RemoveField(model_name="stage", name="on_reply"),
    ]
