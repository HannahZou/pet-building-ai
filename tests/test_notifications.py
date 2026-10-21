import pytest

from pawpal.actors import Actor, AuthorizationError
from pawpal.care_logs import log_activity
from pawpal.database import session_scope
from pawpal.models import (
    ActivityType as A, CompletionStatus as C, DeliveryStatus, Notification, NotificationChannel, NotificationType as N,
)
from pawpal.notifications import (
    MAX_ATTEMPTS, MockEmailProvider, SmtpEmailProvider, deliver_pending, email_provider, mark_read, notify,
    resident_notifications,
)
from pawpal.visit_execution import check_in, check_out
from tests.week3_world import DAY1, at, confirmed_world


def _count(s, **filters):
    q = s.query(Notification)
    for k, v in filters.items():
        q = q.filter(getattr(Notification, k) == v)
    return q.count()


def test_notify_is_idempotent(file_engine):
    w = confirmed_world(file_engine)
    with session_scope(file_engine) as s:
        for _ in range(3):
            notify(s, w.resident, w.booking, N.VISIT_STARTED, "visit:1", "in-app text", "email text")
        assert _count(s, idempotency_key="visit_started:visit:1:in_app") == 1
        assert _count(s, idempotency_key="visit_started:visit:1:email") == 1


def test_delivery_is_idempotent_and_retries(file_engine):
    w = confirmed_world(file_engine)
    with session_scope(file_engine) as s:
        notify(s, w.resident, w.booking, N.VISIT_STARTED, "visit:9", "in-app", "email body")
    flaky = MockEmailProvider(fail_times=1)
    with session_scope(file_engine) as s:
        r1 = deliver_pending(s, flaky)
    with session_scope(file_engine) as s:
        r2 = deliver_pending(s, flaky)
    with session_scope(file_engine) as s:
        r3 = deliver_pending(s, flaky)
        email = s.query(Notification).filter(Notification.channel == NotificationChannel.EMAIL).one()
        assert (r1.failed, r2.sent, r3.sent) == (1, 1, 0)
        assert email.delivery_status == DeliveryStatus.SENT and email.attempts == 2 and email.provider == "mock"
    assert len(flaky.outbox) == 1  # sent exactly once despite three runs


def test_failed_email_stops_after_max_attempts(file_engine):
    w = confirmed_world(file_engine)
    with session_scope(file_engine) as s:
        notify(s, w.resident, w.booking, N.MISSED_VISIT, "visit:5", "in-app", "email")
    broken = MockEmailProvider(fail_times=99)
    for _ in range(MAX_ATTEMPTS + 2):
        with session_scope(file_engine) as s:
            deliver_pending(s, broken)
    with session_scope(file_engine) as s:
        email = s.query(Notification).filter(Notification.channel == NotificationChannel.EMAIL).one()
        assert email.delivery_status == DeliveryStatus.FAILED and email.attempts == MAX_ATTEMPTS
        assert email.last_error == "ConnectionError"


def test_visit_events_notify_once_and_privately(file_engine):
    w = confirmed_world(file_engine)
    v = w.visits[DAY1][0]
    with session_scope(file_engine) as s:
        check_in(s, v, w.caregiver, now=at(DAY1, 7))
        log_activity(s, v, w.caregiver, A.FEEDING, C.NOT_COMPLETED, "PRIVATE-NOTE resident's door code 1234")
        log_activity(s, v, w.caregiver, A.FRESH_WATER, C.COMPLETED)
        check_out(s, v, w.caregiver, now=at(DAY1, 7, 30))
    with session_scope(file_engine) as s:
        rows = s.query(Notification).all()
        assert sorted((n.notification_type.value, n.channel.value) for n in rows) == [
            ("visit_completed", "email"), ("visit_completed", "in_app"),
            ("visit_started", "email"), ("visit_started", "in_app")]
        for n in rows:
            assert "PRIVATE-NOTE" not in n.message and "SECRET-MEDICAL-NOTE" not in n.message
            assert "Meridian" not in n.message  # no building/address details
        emails = [n.message for n in rows if n.channel == NotificationChannel.EMAIL]
        assert all("Open the PawPal app" in m for m in emails)


def test_resident_notification_centre_is_private(file_engine):
    w = confirmed_world(file_engine)
    with session_scope(file_engine) as s:
        notify(s, w.resident, w.booking, N.VISIT_STARTED, "visit:3", "Luna's visit started", "email")
    with session_scope(file_engine) as s:
        mine = resident_notifications(s, w.res_actor)
        assert [n.message for n in mine] == ["Luna's visit started"]
        assert resident_notifications(s, Actor.resident(w.other_resident)) == []
        with pytest.raises(AuthorizationError):
            mark_read(s, mine[0].id, Actor.resident(w.other_resident))
        with pytest.raises(AuthorizationError):
            resident_notifications(s, w.admin_actor)
        mark_read(s, mine[0].id, w.res_actor)
        assert resident_notifications(s, w.res_actor, unread_only=True) == []


def test_email_provider_selection(monkeypatch):
    assert isinstance(email_provider(), MockEmailProvider)  # no credentials -> mock
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("PAWPAL_EMAIL_FROM", "care@example.test")
    provider = email_provider()
    assert isinstance(provider, SmtpEmailProvider) and provider.port == 587
