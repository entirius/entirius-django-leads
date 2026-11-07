# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Q16: one "what to do today" queue for the salesperson, sorted by value.

Read-only. Sections: replies waiting for a person (communicator `needs_review`), the drafts waiting for approval (one
summary row), contacts due today or overdue ("not now" / out-of-office, Q20), companies over the scoring threshold nobody
has written to, and contacts flagged wrong person / possibly left that need a decision. `value` is a plain number
(higher = sooner) so the CMS sorts and the digest ranks without knowing the sections; the weights are constants below.
Communicator has no "reply handled" mark: a reply leaves the queue when a draft or a message went out to the company
after it, or after `LEADS_QUEUE_REPLY_DAYS`."""

from datetime import datetime, time, timedelta
from typing import Any

from django.db.models import Exists, OuterRef
from django.utils import timezone
from django_communicator.enums import MessageStatus, ReplyKind
from django_communicator.models import Message, Reply

from django_leads import settings as leads_settings
from django_leads.enums import ActivityKind, ReplyIntent, StageKind
from django_leads.models import Activity, Channel, Company, Contact
from django_leads.utils.refs import SUBJECT_REF

APPROVALS_PATH = "/leads/approvals"
REPLY_VALUE = {
    ReplyIntent.INTERESTED: 100,
    "": 80,
    ReplyIntent.UNCLEAR: 80,
    ReplyIntent.WRONG_PERSON: 60,
    ReplyIntent.NOT_NOW: 55,
    ReplyIntent.OUT_OF_OFFICE: 50,
    ReplyIntent.NOT_INTERESTED: 40,
    ReplyIntent.UNSUBSCRIBE: 40,
}
DRAFTS_VALUE = 70
TASK_VALUE = 65  # plus one per overdue day, at most 10
SEGMENT_VALUE = {"S1": 80, "S2": 60, "S3": 40, "S4": 20}  # plus points // 10, at most 9
DECISION_VALUE = 45
EXCERPT = 200
FLAGS = ("wrong_person_at", "possibly_left_at")  # possibly_left_at arrives with Q9; absent fields are skipped


def build(channel: Channel, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or timezone.now()
    sections = {
        "reply": _replies(channel, now),
        "task": _tasks(channel, now),
        "hot_company": _hot_companies(channel),
        "decision": _decisions(channel, now),
    }
    drafts = pending_drafts(channel)
    items = [item for rows in sections.values() for item in rows[: leads_settings.LEADS_QUEUE_LIMIT]]
    if drafts:
        items.append(_item("drafts", DRAFTS_VALUE, reason="drafts_waiting", count=drafts, path=APPROVALS_PATH))
    items.sort(key=lambda row: (-row["value"], row["kind"], row["company_id"] or 0))
    counts = {kind: len(rows) for kind, rows in sections.items()} | {"drafts": drafts}
    return {
        "generated_at": now,
        "counts": counts,
        "drafts": {"count": drafts, "approvals_path": APPROVALS_PATH},
        "items": items,
    }


def pending_drafts(channel: Channel) -> int:
    return Message.objects.filter(
        thread__channel__idx=channel.idx,
        thread__subject_ref__startswith="leads.Company:",
        status=MessageStatus.REVIEW_REQUIRED,
    ).count()


def _item(kind: str, value: int, *, company: Company | None = None, contact: Contact | None = None, **extra) -> dict:
    return {
        "kind": kind,
        "value": value,
        "company_id": company.pk if company else None,
        "company_name": company.name if company else "",
        "contact_id": contact.pk if contact else None,
        "contact_email": contact.email if contact else "",
        "reason": extra.pop("reason", ""),
        "due_at": extra.pop("due_at", None),
        "excerpt": extra.pop("excerpt", ""),
        "ref_id": extra.pop("ref_id", None),
        "count": extra.pop("count", None),
        "path": extra.pop("path", ""),
    }


def _replies(channel: Channel, now: datetime) -> list[dict]:
    since = now - timedelta(days=leads_settings.LEADS_QUEUE_REPLY_DAYS)
    replies = (
        Reply.objects.filter(
            thread__channel__idx=channel.idx,
            thread__subject_ref__startswith="leads.Company:",
            needs_review=True,
            kind__in=(ReplyKind.REPLY, ReplyKind.SUSPECTED_OPTOUT),
            received_at__gte=since,
        )
        .select_related("thread")
        .order_by("-received_at")
    )
    rows = []
    for reply in replies:
        match = SUBJECT_REF.match(reply.thread.subject_ref)
        company = Company.objects.filter(pk=int(match.group(1)), channel=channel).first() if match else None
        if company is None or _handled(company, reply):
            continue
        contact = company.contacts.filter(email__iexact=reply.thread.recipient_email).first()
        value = REPLY_VALUE.get(reply.intent, 80)
        rows.append(
            _item(
                "reply", value, company=company, contact=contact, reason=reply.intent or "unclassified",
                due_at=reply.received_at, excerpt=(reply.body_text or "")[:EXCERPT], ref_id=reply.pk,
            )
        )  # fmt: skip
    return sorted(rows, key=lambda row: (-row["value"], row["due_at"]))


def _handled(company: Company, reply: Reply) -> bool:
    later = Activity.objects.filter(company=company, created_at__gt=reply.received_at)
    return later.filter(kind__in=(ActivityKind.SENT, ActivityKind.DRAFT)).exists()


def _tasks(channel: Channel, now: datetime) -> list[dict]:
    end_of_today = datetime.combine(now.date(), time.max, tzinfo=now.tzinfo)
    contacts = Contact.objects.filter(
        company__channel=channel, resume_at__isnull=False, resume_at__lte=end_of_today, anonymised_at__isnull=True
    ).select_related("company")
    rows = []
    for contact in contacts.order_by("resume_at"):
        overdue = max((now.date() - contact.resume_at.date()).days, 0)
        rows.append(
            _item(
                "task", TASK_VALUE + min(overdue, 10), company=contact.company, contact=contact,
                reason=contact.resume_intent or "resume", due_at=contact.resume_at,
            )
        )  # fmt: skip
    return rows


def _hot_companies(channel: Channel) -> list[dict]:
    untouched = ~Exists(
        Activity.objects.filter(company_id=OuterRef("pk"), kind__in=(ActivityKind.SENT, ActivityKind.DRAFT))
    )
    companies = Company.objects.filter(
        untouched, channel=channel, do_not_contact=False, stage__kind=StageKind.OPEN
    ).select_related("stage")
    ids = list(companies.values_list("pk", flat=True))
    latest: dict[int, Activity] = {}
    for activity in Activity.objects.filter(company_id__in=ids, kind=ActivityKind.SCORE).order_by("id"):
        latest[activity.company_id] = activity  # the last one wins
    by_id = {company.pk: company for company in companies}
    rows = []
    for company_id, activity in latest.items():
        segment, points = activity.data.get("segment"), int(activity.data.get("points") or 0)
        if segment not in leads_settings.LEADS_QUEUE_SEGMENTS or activity.data.get("disqualified"):
            continue
        value = SEGMENT_VALUE.get(segment, 20) + min(points // 10, 9)
        rows.append(_item("hot_company", value, company=by_id[company_id], reason=segment, due_at=activity.created_at))
    return sorted(rows, key=lambda row: (-row["value"], row["company_id"]))


def _decisions(channel: Channel, now: datetime) -> list[dict]:
    since = now - timedelta(days=leads_settings.LEADS_QUEUE_REPLY_DAYS)
    rows = []
    for flag in (name for name in FLAGS if any(f.name == name for f in Contact._meta.get_fields())):
        contacts = Contact.objects.filter(
            company__channel=channel,
            company__do_not_contact=False,
            anonymised_at__isnull=True,
            **{f"{flag}__gte": since},
        ).select_related("company")
        for contact in contacts.order_by(f"-{flag}"):
            reason = flag.removesuffix("_at")
            rows.append(
                _item(
                    "decision",
                    DECISION_VALUE,
                    company=contact.company,
                    contact=contact,
                    reason=reason,
                    due_at=getattr(contact, flag),
                )
            )
    return rows
