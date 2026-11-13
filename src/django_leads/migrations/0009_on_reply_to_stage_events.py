# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import migrations

REPLY = "reply"
OUT_OF_OFFICE = "reply:out_of_office"


def stage_events_from_on_reply(apps, schema_editor):
    """Every channel with an `on_reply` stage keeps today's behaviour: any reply moves to the lowest-order such stage
    (backwards too), an out-of-office reply keeps the stage."""
    Stage = apps.get_model("django_leads", "Stage")
    StageEventRule = apps.get_model("django_leads", "StageEventRule")
    targets = {}
    for stage in Stage.objects.filter(on_reply=True).order_by("order", "id"):
        targets.setdefault(stage.channel_id, stage)
    for channel_id, stage in targets.items():
        StageEventRule.objects.get_or_create(
            channel_id=channel_id, event=REPLY, defaults={"target_stage": stage, "forward_only": False}
        )
        StageEventRule.objects.get_or_create(
            channel_id=channel_id, event=OUT_OF_OFFICE, defaults={"target_stage": None}
        )


def on_reply_from_stage_events(apps, schema_editor):
    """The target of every `reply` row gets the flag back; the rows stay until the table goes."""
    Stage = apps.get_model("django_leads", "Stage")
    StageEventRule = apps.get_model("django_leads", "StageEventRule")
    targets = StageEventRule.objects.filter(event=REPLY, target_stage__isnull=False).values("target_stage_id")
    Stage.objects.filter(pk__in=targets).update(on_reply=True)


class Migration(migrations.Migration):
    dependencies = [
        ("django_leads", "0008_outbound_loop"),
    ]

    operations = [
        migrations.RunPython(stage_events_from_on_reply, on_reply_from_stage_events),
    ]
