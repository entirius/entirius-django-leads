# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Upgrade of a database that holds leads 0.4.0 (last migration `0007_lead_types`) to the current head.

The schema is wound back to the state of a 0.4.0 install, rows are written through the historical models, then
`check` and `migrate` run on the current code. Reversing the later migrations and re-applying them also proves each
is reversible with data present. Needs PostgreSQL (`DATABASE_URL`): sqlite rebuilds tables and hides what a server
refuses (pending trigger events after a backfill, for one).
"""

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

APP = "django_leads"
V040 = "0007_lead_types"
BEFORE_JOURNAL = "0016_deal_archived_at"

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="upgrade is proved on PostgreSQL"),
]


def migrate_to(target: str | None) -> None:
    """`None` = the app's latest migration."""
    executor = MigrationExecutor(connection)
    [latest] = executor.loader.graph.leaf_nodes(APP)
    executor.migrate([(APP, target or latest[1])])


def models_at(target: str):
    return MigrationExecutor(connection).loader.project_state((APP, target)).apps


def seed_040() -> dict:
    """What a production database of 0.4.0 holds: channel, stages (one flagged `on_reply`), companies, a contact
    with a legal basis, an anonymised one, an import batch and timeline entries."""
    apps = models_at(V040)
    Channel, Stage, Company, Contact = (
        apps.get_model(APP, name) for name in ("Channel", "Stage", "Company", "Contact")
    )
    Activity, ImportBatch = apps.get_model(APP, "Activity"), apps.get_model(APP, "ImportBatch")
    now = timezone.now()
    channel = Channel.objects.create(idx="upgrade-eu", name="Upgrade EU", retention_days=90)
    new = Stage.objects.create(channel=channel, key="new", label="New", order=0)
    replied = Stage.objects.create(channel=channel, key="replied", label="Replied", order=30, on_reply=True)
    shop = Company.objects.create(
        channel=channel, stage=replied, name="Shop", domain="shop.test", source="csv", stage_entered_at=now
    )
    other = Company.objects.create(
        channel=channel, stage=new, name="Other", domain="other.test", source="form", stage_entered_at=now
    )
    jan = Contact.objects.create(
        company=shop, email="jan@shop.test", source="csv", legal_basis="consent", consent_ref=42
    )
    Contact.objects.create(company=other, email="token@anonymised.invalid", source="csv", anonymised_at=now)
    for company, contact, kind in ((shop, jan, "import"), (shop, jan, "note"), (other, None, "stage")):
        Activity.objects.create(company=company, contact=contact, kind=kind, message=f"{kind} entry", actor="seed")
    ImportBatch.objects.create(channel=channel)
    return {"channel": channel.pk, "shop": shop.pk, "jan": jan.pk}


def test_a_040_database_upgrades_to_head():
    migrate_to(V040)
    call_command("check")  # system checks and ready() must not need a schema newer than the database
    ids = seed_040()

    call_command("check")
    call_command("migrate", verbosity=0)
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)

    apps = models_at("0018_import_column_mapping")
    Company, Contact, Activity = (apps.get_model(APP, name) for name in ("Company", "Contact", "Activity"))
    assert sorted(Company.objects.values_list("domain", flat=True)) == ["other.test", "shop.test"]
    jan = Contact.objects.get(pk=ids["jan"])
    assert (jan.email, jan.legal_basis, jan.consent_ref) == ("jan@shop.test", "consent", 42)
    assert Contact.objects.filter(anonymised_at__isnull=False).count() == 1
    assert Activity.objects.count() == 3
    # 0017: every entry carries its company's channel
    assert set(Activity.objects.values_list("channel_id", flat=True)) == {ids["channel"]}
    # 0018: an existing batch gets an empty mapping
    assert [batch.column_mapping for batch in apps.get_model(APP, "ImportBatch").objects.all()] == [[]]
    # 0009: the stage that was flagged `on_reply` became the channel's reply rule, out-of-office keeps the stage
    rules = apps.get_model(APP, "StageEventRule").objects.filter(channel_id=ids["channel"])
    assert {(rule.event, rule.target_stage.key if rule.target_stage else None) for rule in rules} == {
        ("reply", "replied"),
        ("reply:out_of_office", None),
    }


def test_a_deal_survives_the_history_journal_migration():
    migrate_to(BEFORE_JOURNAL)
    apps = models_at(BEFORE_JOURNAL)
    channel = apps.get_model(APP, "Channel").objects.create(idx="deals-eu", name="Deals EU")
    stage = apps.get_model(APP, "Stage").objects.create(channel=channel, key="new", label="New")
    company = apps.get_model(APP, "Company").objects.create(
        channel=channel, stage=stage, name="Shop", domain="shop.test", source="csv", stage_entered_at=timezone.now()
    )
    deal_stage = apps.get_model(APP, "DealStage").objects.create(channel=channel, key="open", label="Open")
    apps.get_model(APP, "Deal").objects.create(
        channel=channel, company=company, stage=deal_stage, title="Hosting", stage_entered_at=timezone.now()
    )
    apps.get_model(APP, "Activity").objects.create(company=company, kind="deal", message="deal created", actor="seed")

    call_command("check")
    call_command("migrate", verbosity=0)

    head = models_at("0018_import_column_mapping")
    assert [deal.title for deal in head.get_model(APP, "Deal").objects.all()] == ["Hosting"]
    activity = head.get_model(APP, "Activity").objects.get()
    assert (activity.company_id, activity.channel_id) == (company.pk, channel.pk)
