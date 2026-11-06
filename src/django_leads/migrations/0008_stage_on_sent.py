# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("django_leads", "0007_lead_types"),
    ]

    operations = [
        migrations.AddField(
            model_name="stage",
            name="on_sent",
            field=models.BooleanField(
                default=False,
                help_text="Companies move here (forward only) when the first message to them is sent.",
            ),
        ),
    ]
