"""Approval workflow, authorisation and double-booking prevention.

Uses a SQLite *file* database so that confirmations run in their own
write-locked transactions, exactly as in the app.
"""
import threading
from datetime import date, time
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from pawpal import workflow
from pawpal.actors import Actor, AuthorizationError
from pawpal.availability import request_time_off
from pawpal.database import session_scope
from pawpal.models import Booking, BookingStatus as S, ServiceType, Visit, VisitStatus
from pawpal.scheduling import MissingTimeWindowsError, SchedulingError, Window
from pawpal.workflow import (
    InvalidTransition,
    WorkflowError,
    accept_alternative,
    admin_reject,
    cancel_booking,
    confirm_booking,
    prepare_booking,
    reassign_visits,
    resident_approve,
    resident_reject,
)
from tests import factories as f

AM, PM = Window(time(7), time(10)), Window(time(17), time(20))


def _world(engine, caregivers=1, building=f.B1):
    with session_scope(engine) as s:
        r = f.resident(s, building=building)
        other = f.resident(s, name="Other Resident")
        p = f.pet(s, r)
        cgs = [f.caregiver(s, name=f"Carer {i}") for i in range(caregivers)]
        admin = f.manager(s)                       # all buildings
        local = f.manager(s, building=building, name="Local Manager")
        remote = f.manager(s, building="Skyline Court", name="Remote Manager")
        b = f.booking(s, r, p)
        return SimpleNamespace(
            resident=Actor.resident(r.id), other=Actor.resident(other.id), resident_id=r.id, pet_id=p.id,
            cg_ids=[c.id for c in cgs], admin=Actor.manager(admin.id), local=Actor.manager(local.id),
            remote=Actor.manager(remote.id), booking_id=b.id,
        )


def _new_booking(engine, w):
    with session_scope(engine) as s:
        r = s.get(Booking, w.booking_id).resident
        p = s.get(Booking, w.booking_id).pet
        return f.booking(s, r, p).id


def _to_admin_approval(engine, w, booking_id=None, windows=(AM, PM)):
    bid = booking_id or w.booking_id
    with session_scope(engine) as s:
        res = prepare_booking(s, bid, w.resident, windows=list(windows))
        assert res.status == S.PENDING_RESIDENT_APPROVAL
    with session_scope(engine) as s:
        resident_approve(s, bid, w.resident)
    return bid


def _status(engine, bid):
    with session_scope(engine) as s:
        return s.get(Booking, bid).status


# ----------------------------------------------------------------- happy path
def test_full_flow_to_confirmed(file_engine):
    w = _world(file_engine)
    with session_scope(file_engine) as s:
        res = prepare_booking(s, w.booking_id, w.resident, windows=[AM, PM], additional_services=["medication"])
        assert res.quote.total_cents == 32000 and len(res.recommendation.proposals) == 8
    with session_scope(file_engine) as s:
        resident_approve(s, w.booking_id, w.resident)
    result = confirm_booking(w.booking_id, w.local, engine=file_engine)
    assert result.ok and result.status == S.CONFIRMED
    with session_scope(file_engine) as s:
        b = s.get(Booking, w.booking_id)
        assert b.resident_approved_by == w.resident_id and b.resident_approved_at is not None
        assert b.admin_approved_by == w.local.id and b.admin_approved_at is not None
        assert all(v.assignment_status == VisitStatus.CONFIRMED and v.caregiver_id for v in b.visits)
        trail = [(e.from_status, e.to_status, e.actor_role) for e in b.events]
    assert trail == [
        ("draft", "quoted", "resident"),
        ("quoted", "pending_resident_approval", "system"),
        ("pending_resident_approval", "pending_admin_approval", "resident"),
        ("pending_admin_approval", "confirmed", "property_manager"),
    ]


def test_missing_windows_keeps_draft(file_engine):
    w = _world(file_engine)
    with pytest.raises(MissingTimeWindowsError):
        with session_scope(file_engine) as s:
            prepare_booking(s, w.booking_id, w.resident)
    assert _status(file_engine, w.booking_id) == S.DRAFT


def test_no_caregiver_needs_rescheduling_then_alternative(file_engine):
    w = _world(file_engine)
    with session_scope(file_engine) as s:
        res = prepare_booking(s, w.booking_id, w.resident, windows=[AM, Window(time(21), time(23))])
        assert res.status == S.NEEDS_RESCHEDULING and len(res.recommendation.unassigned) == 4
        alts = res.recommendation.alternatives
    for visit_id, options in alts.items():
        alt = options[0]
        assert alt.start == time(20, 30)  # last 30 minutes before the 21:00 end of hours
        with session_scope(file_engine) as s:
            accept_alternative(s, w.booking_id, visit_id, alt.caregiver_id, alt.start, w.resident)
    assert _status(file_engine, w.booking_id) == S.PENDING_RESIDENT_APPROVAL


def test_recalculation_clears_approvals_and_requotes(file_engine):
    w = _world(file_engine)
    _to_admin_approval(file_engine, w)
    with session_scope(file_engine) as s:
        res = prepare_booking(s, w.booking_id, w.resident, duration_minutes=60)
        assert res.quote.total_cents == 8 * 4500
        b = s.get(Booking, w.booking_id)
        assert b.resident_approved_at is None and b.status == S.PENDING_RESIDENT_APPROVAL
        assert sum(q.is_current for q in b.quotes) == 1 and len(b.quotes) == 2


# ------------------------------------------------------- invalid transitions
def test_invalid_transitions(file_engine):
    w = _world(file_engine)
    with pytest.raises(InvalidTransition):
        with session_scope(file_engine) as s:
            resident_approve(s, w.booking_id, w.resident)        # still a draft
    with pytest.raises(InvalidTransition):
        confirm_booking(w.booking_id, w.admin, engine=file_engine)
    _to_admin_approval(file_engine, w)
    with pytest.raises(InvalidTransition):
        with session_scope(file_engine) as s:
            resident_reject(s, w.booking_id, w.resident)        # too late: cancel instead
    with session_scope(file_engine) as s:
        cancel_booking(s, w.booking_id, w.resident, "Plans changed")
        b = s.get(Booking, w.booking_id)
        assert b.status == S.CANCELLED and all(v.assignment_status == VisitStatus.CANCELLED for v in b.visits)
    with pytest.raises(InvalidTransition):
        with session_scope(file_engine) as s:
            cancel_booking(s, w.booking_id, w.resident)


def test_resident_reject_and_admin_reject(file_engine):
    w = _world(file_engine)
    with session_scope(file_engine) as s:
        prepare_booking(s, w.booking_id, w.resident, windows=[AM, PM])
        resident_reject(s, w.booking_id, w.resident, "Too expensive")
    assert _status(file_engine, w.booking_id) == S.REJECTED
    second = _new_booking(file_engine, w)
    _to_admin_approval(file_engine, w, second)
    with session_scope(file_engine) as s:
        admin_reject(s, second, w.admin, "No suitable caregiver")
    assert _status(file_engine, second) == S.REJECTED


# ------------------------------------------------------------- authorisation
def test_unauthorized_approvals(file_engine):
    w = _world(file_engine)
    with session_scope(file_engine) as s:
        prepare_booking(s, w.booking_id, w.resident, windows=[AM, PM])
    for actor in (w.other, w.admin, Actor.caregiver(w.cg_ids[0])):
        with pytest.raises(AuthorizationError):
            with session_scope(file_engine) as s:
                resident_approve(s, w.booking_id, actor)
    with session_scope(file_engine) as s:
        resident_approve(s, w.booking_id, w.resident)
    for actor in (w.resident, w.other, Actor.caregiver(w.cg_ids[0]), w.remote, Actor.manager(9999)):
        with pytest.raises(AuthorizationError):
            confirm_booking(w.booking_id, actor, engine=file_engine)
    assert _status(file_engine, w.booking_id) == S.PENDING_ADMIN_APPROVAL
    with pytest.raises(AuthorizationError):
        with session_scope(file_engine) as s:
            prepare_booking(s, w.booking_id, w.other, windows=[AM, PM])


def test_confirmation_requires_resident_approval(file_engine):
    w = _world(file_engine)
    with session_scope(file_engine) as s:
        prepare_booking(s, w.booking_id, w.resident, windows=[AM, PM])
        b = s.get(Booking, w.booking_id)
        b.status = S.PENDING_ADMIN_APPROVAL  # simulate a bug/bypass skipping the resident
    with pytest.raises(WorkflowError, match="resident has not approved"):
        confirm_booking(w.booking_id, w.admin, engine=file_engine)


def test_final_check_failure_moves_to_needs_rescheduling(file_engine):
    w = _world(file_engine)
    _to_admin_approval(file_engine, w)
    with session_scope(file_engine) as s:
        cg = s.get(Booking, w.booking_id).visits[0].caregiver_id
        request_time_off(s, cg, Actor.caregiver(cg), date(2026, 11, 7), date(2026, 11, 7), "Sick")
    result = confirm_booking(w.booking_id, w.admin, engine=file_engine)
    assert not result.ok and result.status == S.NEEDS_RESCHEDULING
    assert {c.kind for c in result.conflicts} == {"time_off"}
    assert _status(file_engine, w.booking_id) == S.NEEDS_RESCHEDULING


# --------------------------------------------------------- manual reassignment
def test_manager_reassignment_is_validated(file_engine):
    w = _world(file_engine, caregivers=2)
    _to_admin_approval(file_engine, w)
    with session_scope(file_engine) as s:
        b = s.get(Booking, w.booking_id)
        current = b.visits[0].caregiver_id
        other = next(c for c in w.cg_ids if c != current)
        ids = [v.id for v in b.visits]
    with session_scope(file_engine) as s:
        reassign_visits(s, w.booking_id, ids, other, w.admin)
    with session_scope(file_engine) as s:
        assert {v.caregiver_id for v in s.get(Booking, w.booking_id).visits} == {other}
        request_time_off(s, current, Actor.caregiver(current), date(2026, 11, 6), date(2026, 11, 6))
    with pytest.raises(SchedulingError, match="time off"):
        with session_scope(file_engine) as s:
            reassign_visits(s, w.booking_id, ids[:1], current, w.admin)
    with pytest.raises(AuthorizationError):
        with session_scope(file_engine) as s:
            reassign_visits(s, w.booking_id, ids, other, w.resident)
    assert confirm_booking(w.booking_id, w.admin, engine=file_engine).ok


# --------------------------------------------------------- double booking
def _two_competing_bookings(engine):
    """Two bookings, both pending admin approval, with the same caregiver at the same times."""
    tight = [Window(time(7), time(7, 30)), Window(time(17), time(17, 30))]  # no room to shift
    w = _world(engine)
    first = _to_admin_approval(engine, w, windows=tight)
    second = _new_booking(engine, w)
    with session_scope(engine) as s:
        res = prepare_booking(s, second, w.resident, windows=tight)
        assert res.status == S.NEEDS_RESCHEDULING  # matcher avoids the soft hold
        ids = [v.id for v in s.get(Booking, second).visits]
    with session_scope(engine) as s:
        reassign_visits(s, second, ids, w.cg_ids[0], w.admin)  # manager forces the same caregiver
    with session_scope(engine) as s:
        resident_approve(s, second, w.resident)
    return w, first, second


def test_double_booking_prevented_sequentially(file_engine):
    w, first, second = _two_competing_bookings(file_engine)
    assert confirm_booking(first, w.admin, engine=file_engine).ok
    result = confirm_booking(second, w.admin, engine=file_engine)
    assert not result.ok and result.status == S.NEEDS_RESCHEDULING
    assert {c.kind for c in result.conflicts} == {"overlap"}


def test_concurrent_confirmations_only_one_wins(file_engine):
    w, first, second = _two_competing_bookings(file_engine)
    barrier = threading.Barrier(2)
    results = {}

    def run(bid):
        barrier.wait()
        results[bid] = confirm_booking(bid, w.admin, engine=file_engine)

    threads = [threading.Thread(target=run, args=(b,)) for b in (first, second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(r.ok for r in results.values()) == [False, True]
    with session_scope(file_engine) as s:
        confirmed = s.query(Visit).filter(Visit.assignment_status == VisitStatus.CONFIRMED).count()
    assert confirmed == 8


def test_database_trigger_is_last_line_of_defence(file_engine, monkeypatch):
    w, first, second = _two_competing_bookings(file_engine)
    assert confirm_booking(first, w.admin, engine=file_engine).ok
    monkeypatch.setattr(workflow, "check_booking_schedule", lambda *a, **k: [])  # pretend the app check has a bug
    result = confirm_booking(second, w.admin, engine=file_engine)
    assert not result.ok and "same caregiver" in result.message
    assert _status(file_engine, second) == S.NEEDS_RESCHEDULING
    with pytest.raises(IntegrityError, match="caregiver_double_booked"):
        with session_scope(file_engine) as s:
            v = s.query(Visit).filter(Visit.booking_id == second).first()
            v.assignment_status = VisitStatus.CONFIRMED


def test_write_while_a_read_transaction_is_open_does_not_lock(file_engine):
    """Regression: a page's open read session must not block a write (WAL mode)."""
    import time as _time

    w = _world(file_engine)
    with session_scope(file_engine) as reader:
        assert reader.get(Booking, w.booking_id).status == S.DRAFT  # read txn now open
        started = _time.monotonic()
        with session_scope(file_engine) as writer:
            prepare_booking(writer, w.booking_id, w.resident, windows=[AM, PM])
        assert _time.monotonic() - started < 5
    assert _status(file_engine, w.booking_id) == S.PENDING_RESIDENT_APPROVAL
