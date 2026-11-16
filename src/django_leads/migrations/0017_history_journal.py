# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import django.db.models.deletion
from django.db import migrations, models


def fill_channel(apps, schema_editor):
    """Every existing entry is about a company: its channel is the company's."""
    Activity = apps.get_model("django_leads", "Activity")
    Company = apps.get_model("django_leads", "Company")
    channel_of = models.Subquery(Company.objects.filter(pk=models.OuterRef("company_id")).values("channel_id")[:1])
    Activity.objects.filter(channel__isnull=True).update(channel_id=channel_of)


def drop_companyless(apps, schema_editor):
    """Reverse: entries about the channel itself (settings, no company) cannot live in the old schema."""
    apps.get_model("django_leads", "Activity").objects.filter(company__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("django_leads", "0016_deal_archived_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="activity",
            name="channel",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="activities",
                to="django_leads.channel",
            ),
        ),
        migrations.AlterField(
            model_name="activity",
            name="company",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="activities",
                to="django_leads.company",
            ),
        ),
        # after the company turns nullable, before the indexes (a backfill under three indexes is slower); reversed
        # before the company is NOT NULL again, so the entries without a company (settings) go first
        migrations.RunPython(fill_channel, drop_companyless),
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
                    ("settings", "Settings"),
                ],
                max_length=32,
            ),
        ),
        migrations.AddIndex(
            model_name="activity",
            index=models.Index(fields=["channel", "created_at"], name="django_lead_act_chan_idx"),
        ),
        migrations.AddIndex(
            model_name="activity",
            index=models.Index(fields=["channel", "actor", "created_at"], name="django_lead_act_actor_idx"),
        ),
        migrations.AddIndex(
            model_name="activity",
            index=models.Index(fields=["channel", "kind", "created_at"], name="django_lead_act_kind_idx"),
        ),
    ]
