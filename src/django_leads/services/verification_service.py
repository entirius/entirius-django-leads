# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Address verification (Q5): syntax, disposable domain, DNS (domain exists, MX), an optional SMTP RCPT probe and an
optional external API. The result lives on the contact (`email_status`, reason code, `email_verified_at`).

Nothing here talks to the network on its own: the DNS resolver and the SMTP prober are injected (`use_resolver`,
`use_prober`, or the `resolver=` / `prober=` arguments), the module suite installs stubs, and the whole loop is off
until `LEADS_VERIFY_ENABLED`. Only `invalid` blocks outreach (`recipient_service.block_reason`)."""

import logging
import re
import smtplib
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.module_loading import import_string

from django_leads import settings as leads_settings
from django_leads.enums import ActivityKind, EmailStatus
from django_leads.models import Contact
from django_leads.services import activity_service
from django_leads.utils.emails import normalize_email

logger = logging.getLogger(__name__)

HARD_BOUNCE = "hard_bounce"
_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


@dataclass(frozen=True)
class Verdict:
    status: str
    reason: str


@dataclass(frozen=True)
class DnsAnswer:
    """What the resolver found for a domain. `error` set = the lookup itself failed (timeout, no nameserver)."""

    exists: bool = True
    mx_hosts: tuple[str, ...] = ()
    null_mx: bool = False
    has_address: bool = False
    error: str = ""


class Resolver(Protocol):
    def lookup(self, domain: str) -> DnsAnswer: ...


class Prober(Protocol):
    def probe(self, mx_host: str, email: str) -> Verdict | None:
        """valid / invalid when the server answered the RCPT clearly, None when inconclusive."""


class DnsPythonResolver:
    """The real resolver (dnspython). Never used by the module suite."""

    def lookup(self, domain: str) -> DnsAnswer:
        try:
            import dns.exception
            import dns.resolver
        except ImportError:
            return DnsAnswer(error="no_resolver")
        resolver = dns.resolver.Resolver()
        resolver.lifetime = leads_settings.LEADS_VERIFY_DNS_TIMEOUT_S
        try:
            answers = resolver.resolve(domain, "MX")
        except dns.resolver.NXDOMAIN:
            return DnsAnswer(exists=False)
        except dns.resolver.NoAnswer:
            return self._address_fallback(resolver, domain)
        except (dns.exception.DNSException, OSError):
            return DnsAnswer(error="dns_error")
        hosts = sorted((r.preference, str(r.exchange).rstrip(".")) for r in answers)
        if len(hosts) == 1 and hosts[0][1] == "":
            return DnsAnswer(null_mx=True)
        return DnsAnswer(mx_hosts=tuple(host for _, host in hosts if host))

    @staticmethod
    def _address_fallback(resolver, domain: str) -> DnsAnswer:
        import dns.exception
        import dns.resolver

        for record in ("A", "AAAA"):
            try:
                resolver.resolve(domain, record)
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
                continue
            except (dns.exception.DNSException, OSError):
                return DnsAnswer(error="dns_error")
            return DnsAnswer(has_address=True)
        return DnsAnswer()


class SmtpProber:
    """RCPT probe: connect to the MX, `MAIL FROM`, `RCPT TO`, quit — no message is sent. Rate limited per minute."""

    def probe(self, mx_host: str, email: str) -> Verdict | None:
        sender = leads_settings.LEADS_VERIFY_SMTP_FROM
        if not sender or not _take_probe_slot():
            return None
        try:
            with smtplib.SMTP(mx_host, 25, timeout=leads_settings.LEADS_VERIFY_SMTP_TIMEOUT_S) as server:
                server.ehlo_or_helo_if_needed()
                server.mail(sender)
                code, _ = server.rcpt(email)
        except (smtplib.SMTPException, OSError):
            return None
        if code in (250, 251):
            return Verdict(EmailStatus.VALID, "smtp_accepted")
        if code in (550, 551, 553):
            return Verdict(EmailStatus.INVALID, "mailbox_rejected")
        return None


def _take_probe_slot() -> bool:
    """At most `LEADS_VERIFY_SMTP_PER_MINUTE` probes per minute (shared cache, so also across workers)."""
    key = f"leads:verify:probe:{int(time.time() // 60)}"
    cache.add(key, 0, timeout=120)
    return cache.incr(key) <= leads_settings.LEADS_VERIFY_SMTP_PER_MINUTE


_resolver: Resolver | None = None
_prober: Prober | None = None


def use_resolver(resolver: Resolver | None) -> None:
    """Install the resolver every check uses (None = the real one). The module suite installs a stub."""
    global _resolver
    _resolver = resolver


def use_prober(prober: Prober | None) -> None:
    global _prober
    _prober = prober


# --- the check -----------------------------------------------------------------------------------------------------


def verify_address(email: str, *, resolver: Resolver | None = None, prober: Prober | None = None) -> Verdict:
    """The verdict for one address; pure apart from the injected resolver, prober and external hook."""
    email = normalize_email(email)
    if reason := _syntax_problem(email):
        return Verdict(EmailStatus.INVALID, reason)
    domain = email.rsplit("@", 1)[1]
    if _is_disposable(domain):
        return Verdict(EmailStatus.INVALID, "disposable")
    answer = (resolver or _resolver or DnsPythonResolver()).lookup(domain)
    verdict = _dns_verdict(answer)
    if verdict.status == EmailStatus.VALID and leads_settings.LEADS_VERIFY_SMTP_PROBE:
        verdict = _probe(answer, email, prober) or verdict
    if verdict.status != EmailStatus.INVALID:
        verdict = _external(email) or verdict
    return verdict


def _syntax_problem(email: str) -> str:
    if not email or len(email) > 254 or email.count("@") != 1:
        return "syntax"
    local, domain = email.split("@")
    if len(local) > 64 or ".." in email or local.startswith(".") or local.endswith(".") or not _DOMAIN_RE.match(domain):
        return "syntax"
    try:
        validate_email(email)
    except ValidationError:
        return "syntax"
    return ""


def _is_disposable(domain: str) -> bool:
    listed = {item.lower() for item in leads_settings.LEADS_DISPOSABLE_DOMAINS}
    parts = domain.split(".")
    return any(".".join(parts[index:]) in listed for index in range(len(parts) - 1))


def _dns_verdict(answer: DnsAnswer) -> Verdict:
    if answer.error:
        return Verdict(EmailStatus.UNKNOWN, answer.error)
    if not answer.exists:
        return Verdict(EmailStatus.INVALID, "domain_not_found")
    if answer.null_mx:
        return Verdict(EmailStatus.INVALID, "null_mx")
    if answer.mx_hosts:
        return Verdict(EmailStatus.VALID, "mx_ok")
    if answer.has_address:
        return Verdict(EmailStatus.RISKY, "no_mx_address_fallback")
    return Verdict(EmailStatus.INVALID, "no_mail_server")


def _probe(answer: DnsAnswer, email: str, prober: Prober | None) -> Verdict | None:
    return (prober or _prober or SmtpProber()).probe(answer.mx_hosts[0], email)


def _external(email: str) -> Verdict | None:
    """Hook for an external verification API (`LEADS_VERIFY_EXTERNAL`); a failure is logged and ignored."""
    path = leads_settings.LEADS_VERIFY_EXTERNAL
    if not path:
        return None
    try:
        answer = import_string(path)(email)
    except Exception as error:  # noqa: BLE001 — a vendor outage must not stop outreach
        logger.warning("leads: external verification failed (%s)", type(error).__name__)
        return None
    if not answer or answer[0] not in EmailStatus.values:
        return None
    return Verdict(answer[0], str(answer[1])[:64])


# --- the contact ---------------------------------------------------------------------------------------------------


def verifiable(contact: Contact) -> bool:
    return bool(contact.email) and contact.anonymised_at is None


def needs_check(contact: Contact, *, now=None) -> bool:
    """Never verified, or verified longer ago than `LEADS_VERIFY_MAX_AGE_DAYS`. A hard bounce is final."""
    if not verifiable(contact) or contact.email_status_reason == HARD_BOUNCE:
        return False
    if contact.email_verified_at is None:
        return True
    return contact.email_verified_at < (now or timezone.now()) - timedelta(
        days=leads_settings.LEADS_VERIFY_MAX_AGE_DAYS
    )


def verify_contact(contact: Contact, *, actor: str = "verification") -> Verdict:
    """Check the address and store the result; a change of status that matters leaves a timeline entry."""
    verdict = verify_address(contact.email)
    return _store(contact, verdict, actor)


def _store(contact: Contact, verdict: Verdict, actor: str) -> Verdict:
    previous = contact.email_status
    contact.email_status, contact.email_status_reason = verdict.status, verdict.reason
    contact.email_verified_at = timezone.now()
    contact.save(update_fields=["email_status", "email_status_reason", "email_verified_at", "modified_at"])
    flagged = (EmailStatus.INVALID, EmailStatus.RISKY)
    if previous != verdict.status and (verdict.status in flagged or previous in flagged):
        data = {"from": previous, "to": verdict.status, "reason": verdict.reason}
        message = f"address {verdict.status}: {verdict.reason}"
        activity_service.record(
            contact.company, ActivityKind.VERIFICATION, message, contact=contact, data=data, actor=actor
        )
    return verdict


def ensure_fresh(contact: Contact) -> None:
    """Called by the outreach gate before it locks anything: with the loop on, an unverified or stale address is
    checked now, so a bad one blocks this very draft. Off = no-op."""
    if leads_settings.LEADS_VERIFY_ENABLED and needs_check(contact):
        verify_contact(contact, actor="verification:before_send")


def presend_check(email: str) -> str | None:
    """For the sender (communicator): the reason code when the address must not be mailed now, else None. Stored
    status only plus a live check when the loop is on and the address is stale; never raises for an unknown address."""
    contact = Contact.objects.filter(email__iexact=normalize_email(email)).select_related("company").first()
    if contact is None:
        return None
    ensure_fresh(contact)
    return f"email_{contact.email_status}" if contact.email_status == EmailStatus.INVALID else None


def stale_contacts(*, now=None) -> QuerySet[Contact]:
    """Contacts due for a check: never verified first, then the oldest; a hard bounce is never re-checked."""
    cutoff = (now or timezone.now()) - timedelta(days=leads_settings.LEADS_VERIFY_MAX_AGE_DAYS)
    due = Q(email_verified_at__isnull=True) | Q(email_verified_at__lt=cutoff)
    contacts = Contact.objects.filter(due, anonymised_at__isnull=True).exclude(email="")
    return (
        contacts.exclude(email_status_reason=HARD_BOUNCE).select_related("company").order_by("email_verified_at", "pk")
    )


def reverify_stale(*, limit: int | None = None) -> dict[str, int]:
    """The scheduled run: counts per status of the contacts checked now. Off = nothing is checked."""
    if not leads_settings.LEADS_VERIFY_ENABLED:
        return {}
    counts: dict[str, int] = {}
    for contact in list(stale_contacts()[: limit or leads_settings.LEADS_VERIFY_BATCH]):
        verdict = verify_contact(contact, actor="verification:scheduled")
        counts[verdict.status] = counts.get(verdict.status, 0) + 1
    return counts


# --- hard bounces --------------------------------------------------------------------------------------------------


def mark_hard_bounce(contact: Contact, *, actor: str = "bounce") -> None:
    """A hard bounce is proof the mailbox is gone: `invalid`, final (the scheduled run never re-checks it)."""
    if contact.email_status == EmailStatus.INVALID and contact.email_status_reason == HARD_BOUNCE:
        return
    _store(contact, Verdict(EmailStatus.INVALID, HARD_BOUNCE), actor)


def sync_bounces(*, now=None) -> int:
    """Hard bounces communicator stored in the last `LEADS_BOUNCE_SYNC_DAYS` days become `invalid` contacts. Catches
    what a missed signal left behind; idempotent. Returns the number of contacts marked."""
    from django_communicator.enums import ReplyKind
    from django_communicator.models import Reply

    since = (now or timezone.now()) - timedelta(days=leads_settings.LEADS_BOUNCE_SYNC_DAYS)
    replies = Reply.objects.filter(kind=ReplyKind.BOUNCE_HARD, received_at__gte=since)
    marked = 0
    for reply in replies.filter(thread__subject_ref__startswith="leads.Company:").select_related("thread"):
        marked += _mark_thread_recipient(reply.thread)
    return marked


def mark_thread_bounce(thread) -> int:
    """One hard-bounced thread (the `message_bounced` signal): its recipient contact becomes `invalid / hard_bounce`
    at once. Returns 1 when a contact was marked, 0 for a thread that is not ours or already marked."""
    return _mark_thread_recipient(thread)


def _mark_thread_recipient(thread) -> int:
    from django_leads.utils.refs import company_from_ref

    company = company_from_ref(thread.subject_ref)
    contact = company.contacts.filter(email__iexact=thread.recipient_email).first() if company else None
    if contact is None or contact.email_status_reason == HARD_BOUNCE:
        return 0
    contact.company = company
    mark_hard_bounce(contact)
    return 1
