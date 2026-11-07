# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""One "do not contact" list for every mailbox, domain and channel (Q7). The sender routes of Q3 are mailboxes inside
one communicator channel, so a communicator suppression already covers them; this service closes the gap between
leads channels: an opt-out, a `do_not_contact` company or a communicator suppression made anywhere stops the same
address or domain everywhere. (ecom.house and entirius.com are one legal person for opt-outs — the owner's statement.)"""

from django.db.models import QuerySet
from django.utils import timezone
from django_communicator.enums import SuppressionKind
from django_communicator.models import Channel as CommunicatorChannel
from django_communicator.models import Suppression
from django_communicator.services import suppression_service

from django_leads.enums import ActivityKind
from django_leads.models import Company, Contact
from django_leads.services import activity_service
from django_leads.utils.domains import email_domain, registrable_domain
from django_leads.utils.emails import normalize_email


def shared_block(contact: Contact) -> str | None:
    """Reason code when the same address or domain is on the list through ANOTHER channel; the contact's own
    channel is covered by the gate's own checks and by communicator (`suppressed`)."""
    email = normalize_email(contact.email)
    company = contact.company
    if email and Contact.objects.filter(email__iexact=email, opt_out_at__isnull=False).exclude(pk=contact.pk).exists():
        return "opted_out_elsewhere"
    if Company.objects.filter(domain=company.domain, do_not_contact=True).exclude(pk=company.pk).exists():
        return "domain_do_not_contact_elsewhere"
    if email and _suppressions_elsewhere(company, email).exists():
        return "suppressed_elsewhere"
    return None


def _suppressions_elsewhere(company: Company, email: str) -> QuerySet[Suppression]:
    try:
        domain = email_domain(email)
    except ValueError:
        domain = ""
    listed = Suppression.objects.filter(kind=SuppressionKind.EMAIL, value=email) | Suppression.objects.filter(
        kind=SuppressionKind.DOMAIN, value=domain
    )
    return listed.exclude(channel__idx=company.channel.idx).filter(channel__isnull=False)


def add(*, email: str = "", domain: str = "", reason: str, actor: str) -> dict[str, int]:
    """Put an address or a registrable domain on the list everywhere: matching contacts get `opt_out_at` (email) or
    their companies `do_not_contact` (domain) in every channel, and every communicator channel gets a suppression.
    Idempotent. Exactly one of `email`, `domain`. Raises `ValueError` on an unusable value."""
    if bool(email) == bool(domain):
        raise ValueError("give exactly one of email or domain")
    if email:
        return _add_email(normalize_email(email), reason, actor)
    return _add_domain(registrable_domain(domain), reason, actor)


def _add_email(email: str, reason: str, actor: str) -> dict[str, int]:
    suppression_service.normalise_value(SuppressionKind.EMAIL, email)  # ValueError on garbage
    for channel in CommunicatorChannel.objects.all():
        suppression_service.suppress_email(channel, email, reason=reason)
    contacts = Contact.objects.filter(email__iexact=email, opt_out_at__isnull=True).select_related("company")
    count = 0
    for contact in contacts:
        Contact.objects.filter(pk=contact.pk).update(opt_out_at=timezone.now())
        activity_service.record(
            contact.company, ActivityKind.OPTOUT, f"do not contact: {reason}"[:255], contact=contact, actor=actor
        )
        count += 1
    return {"contacts": count, "companies": 0}


def _add_domain(domain: str, reason: str, actor: str) -> dict[str, int]:
    for channel in CommunicatorChannel.objects.all():
        try:
            suppression_service.create_suppression(channel, kind=SuppressionKind.DOMAIN, value=domain, reason=reason)
        except suppression_service.DuplicateSuppressionError:
            continue
    companies = list(Company.objects.filter(domain=domain, do_not_contact=False))
    for company in companies:
        Company.objects.filter(pk=company.pk).update(do_not_contact=True)
        activity_service.record(company, ActivityKind.OPTOUT, f"do not contact: {reason}"[:255], actor=actor)
    return {"contacts": 0, "companies": len(companies)}


def listing(channel_idx: str = "") -> dict:
    """What is on the list: opted-out contacts (address hash only), blocked companies and communicator rows. Contact
    emails are shown — staff with `leads.companies` already see them."""
    contacts = Contact.objects.filter(opt_out_at__isnull=False).select_related("company__channel")
    companies = Company.objects.filter(do_not_contact=True).select_related("channel")
    suppressions = Suppression.objects.select_related("channel")
    if channel_idx:
        contacts = contacts.filter(company__channel__idx=channel_idx)
        companies = companies.filter(channel__idx=channel_idx)
    return {
        "contacts": [
            {"email": c.email, "channel": c.company.channel.idx, "opt_out_at": c.opt_out_at} for c in contacts[:500]
        ],
        "companies": [{"domain": c.domain, "channel": c.channel.idx} for c in companies[:500]],
        "suppressions": [
            {"kind": s.kind, "value": s.value, "channel": s.channel.idx if s.channel else None, "reason": s.reason}
            for s in suppressions.order_by("kind", "value")[:500]
            if s.kind != SuppressionKind.EMAIL_TOKEN
        ],
    }
