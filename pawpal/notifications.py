"""Resident notifications: in-app centre + optional email.

Design
------
* **Outbox:** notifications are written in the *same transaction* as the event
  that caused them (check-in, report approval...). Email delivery happens
  afterwards (``deliver_pending``), so a failed email never undoes a check-in.
* **Idempotent:** every notification has a unique ``idempotency_key``
  (e.g. ``visit_started:visit:42:email``). Creating it twice returns the existing
  row; delivering it twice sends once.
* **Privacy:** messages contain the pet's name and a time at most - never
  caregiver notes, observations, medical details or addresses. Emails are even
  shorter and point to the app.
* **Email:** ``SmtpEmailProvider`` when ``SMTP_HOST`` / ``PAWPAL_EMAIL_FROM`` are
  set, otherwise ``MockEmailProvider`` (records messages in memory; nothing leaves
  the machine). AI-generated reports are never emailed; residents are only told
  that an *approved* report is available.
"""
from __future__ import annotations

import logging
import os
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .actors import SYSTEM, Actor, AuthorizationError
from .audit import record
from .models import (
    DeliveryStatus,
    Notification,
    NotificationChannel,
    NotificationType,
    Resident,
)
from .timeutil import utc_now

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3

EMAIL_SUBJECTS = {
    NotificationType.VISIT_STARTED: "PawPal: visit started",
    NotificationType.VISIT_COMPLETED: "PawPal: visit completed",
    NotificationType.DAILY_REPORT_AVAILABLE: "PawPal: daily care report ready",
    NotificationType.MISSED_VISIT: "PawPal: a visit was missed",
    NotificationType.CARE_ISSUE: "PawPal: please review a care note",
}


# ------------------------------------------------------------------ providers
class EmailProvider(Protocol):
    name: str

    def send(self, to: str, subject: str, body: str) -> None: ...


@dataclass
class MockEmailProvider:
    """Development provider: keeps messages in memory, sends nothing."""

    name: str = "mock"
    outbox: list[dict] = field(default_factory=list)
    fail_times: int = 0  # for tests: raise this many times before succeeding

    def send(self, to: str, subject: str, body: str) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("mock email failure")
        self.outbox.append({"to": to, "subject": subject, "body": body})


@dataclass
class SmtpEmailProvider:
    host: str
    port: int
    sender: str
    username: str | None = None
    password: str | None = None
    name: str = "smtp"

    def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=15) as smtp:
            smtp.starttls()
            if self.username:
                smtp.login(self.username, self.password or "")
            smtp.send_message(msg)


_MOCK = MockEmailProvider()


def email_provider() -> EmailProvider:
    host, sender = os.getenv("SMTP_HOST"), os.getenv("PAWPAL_EMAIL_FROM")
    if host and sender:
        return SmtpEmailProvider(
            host=host, port=int(os.getenv("SMTP_PORT", "587")), sender=sender,
            username=os.getenv("SMTP_USERNAME"), password=os.getenv("SMTP_PASSWORD"),
        )
    return _MOCK


def email_enabled() -> bool:
    return os.getenv("PAWPAL_EMAIL_NOTIFICATIONS", "true").lower() not in ("0", "false", "no")


# ------------------------------------------------------------------- creation
def notify(
    session: Session,
    resident_id: int,
    booking_id: int | None,
    ntype: NotificationType,
    scope: str,
    in_app_message: str,
    email_body: str,
) -> list[Notification]:
    """Create the in-app (+ email) notification once per ``scope``. Safe to call repeatedly."""
    channels = [NotificationChannel.IN_APP] + ([NotificationChannel.EMAIL] if email_enabled() else [])
    out = []
    for channel in channels:
        key = f"{ntype.value}:{scope}:{channel.value}"
        existing = session.scalar(select(Notification).where(Notification.idempotency_key == key))
        if existing:
            out.append(existing)
            continue
        row = Notification(
            resident_id=resident_id, booking_id=booking_id, notification_type=ntype, channel=channel,
            message=in_app_message if channel == NotificationChannel.IN_APP else email_body,
            idempotency_key=key,
            delivery_status=DeliveryStatus.SENT if channel == NotificationChannel.IN_APP else DeliveryStatus.PENDING,
            provider="in_app" if channel == NotificationChannel.IN_APP else None,
            attempts=1 if channel == NotificationChannel.IN_APP else 0,
            sent_at=utc_now() if channel == NotificationChannel.IN_APP else None,
        )
        try:
            with session.begin_nested():  # a concurrent duplicate only rolls back this insert
                session.add(row)
                session.flush()
        except IntegrityError:
            row = session.scalar(select(Notification).where(Notification.idempotency_key == key))
        out.append(row)
    return out


# ------------------------------------------------------------------- delivery
@dataclass
class DeliveryResult:
    sent: int = 0
    failed: int = 0
    skipped: int = 0


def deliver_pending(session: Session, provider: EmailProvider | None = None, limit: int = 100) -> DeliveryResult:
    """Send pending/failed emails (up to MAX_ATTEMPTS each). Already-sent rows are never resent."""
    provider = provider or email_provider()
    result = DeliveryResult()
    rows = session.scalars(
        select(Notification)
        .where(
            Notification.channel == NotificationChannel.EMAIL,
            Notification.delivery_status.in_([DeliveryStatus.PENDING, DeliveryStatus.FAILED]),
            Notification.attempts < MAX_ATTEMPTS,
        )
        .order_by(Notification.id)
        .limit(limit)
    ).all()
    for n in rows:
        resident = session.get(Resident, n.resident_id)
        n.attempts += 1
        try:
            provider.send(resident.email, EMAIL_SUBJECTS[n.notification_type], n.message)
        except Exception as exc:  # network/auth errors: keep for retry
            n.delivery_status = DeliveryStatus.FAILED
            n.last_error = type(exc).__name__
            result.failed += 1
            record(session, "notification", n.id, "email_failed", SYSTEM, attempts=n.attempts)
            logger.warning("Email notification %s failed (%s)", n.id, type(exc).__name__)
        else:
            n.delivery_status = DeliveryStatus.SENT
            n.provider = provider.name
            n.sent_at = utc_now()
            n.last_error = None
            result.sent += 1
            record(session, "notification", n.id, "email_sent", SYSTEM, provider=provider.name)
        session.flush()
    return result


# -------------------------------------------------------------------- reading
def resident_notifications(session: Session, actor: Actor, unread_only: bool = False) -> list[Notification]:
    if actor.role != "resident":
        raise AuthorizationError("Only residents have a notification centre.")
    q = select(Notification).where(
        Notification.resident_id == actor.id, Notification.channel == NotificationChannel.IN_APP
    )
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    return list(session.scalars(q.order_by(Notification.created_at.desc(), Notification.id.desc())))


def mark_read(session: Session, notification_id: int, actor: Actor) -> None:
    n = session.get(Notification, notification_id)
    if n is None or actor.role != "resident" or n.resident_id != actor.id:
        raise AuthorizationError("You can only manage your own notifications.")
    if n.read_at is None:
        n.read_at = utc_now()
        session.flush()


def delivery_stats(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(Notification.channel, Notification.delivery_status)
    ).all()
    stats = {"total": len(rows), "sent": 0, "pending": 0, "failed": 0, "email_sent": 0, "email_failed": 0, "email_pending": 0}
    for channel, status in rows:
        stats[status.value] += 1
        if channel == NotificationChannel.EMAIL:
            stats[f"email_{status.value}"] += 1
    return stats
