"""Caregiver check-in / check-out and visit execution status.

    Scheduled -> In Progress -> Completed | Needs Review
    Scheduled -> Missed (local day ended with no check-in) | Cancelled (booking cancelled)
    In Progress -> Needs Review (local day ended with no check-out)

Rules: only the assigned caregiver checks in/out; only visits of *confirmed*
bookings can start, and only on the visit's local date (building time zone);
check-out requires a check-in and is strictly later; timestamps are generated
by the server (``utc_now``) - callers cannot supply them except tests; duplicate
check-ins/outs are rejected (status check + optimistic version); a visit is never
completed just because time passed. Every change is written to ``audit_events``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .actors import SYSTEM, Actor, AuthorizationError
from .audit import record
from .care_logs import fill_unrecorded_essentials, review_reasons
from .models import (
    Booking,
    BookingStatus,
    ExecutionStatus as E,
    NotificationType as N,
    PropertyManager,
    Visit,
    VisitExecution,
    VisitStatus,
)
from .notifications import notify
from .timeutil import as_utc, fmt_local, local_today, utc_now, visit_tz

TRANSITIONS: dict[E, set[E]] = {
    E.SCHEDULED: {E.IN_PROGRESS, E.MISSED, E.CANCELLED},
    E.IN_PROGRESS: {E.COMPLETED, E.NEEDS_REVIEW},
    E.COMPLETED: set(),
    E.NEEDS_REVIEW: set(),
    E.MISSED: set(),
    E.CANCELLED: set(),
}
TERMINAL = {E.COMPLETED, E.NEEDS_REVIEW, E.MISSED, E.CANCELLED}
LABELS = {
    E.SCHEDULED: "Scheduled", E.IN_PROGRESS: "In Progress", E.COMPLETED: "Completed",
    E.MISSED: "Missed", E.CANCELLED: "Cancelled", E.NEEDS_REVIEW: "Needs Review",
}
OVERDUE_AFTER = timedelta(minutes=60)


class ExecutionError(Exception):
    pass


class InvalidVisitTransition(ExecutionError):
    pass


def _transition(session: Session, ex: VisitExecution, to: E, actor: Actor, **details) -> None:
    if to not in TRANSITIONS[ex.execution_status]:
        raise InvalidVisitTransition(
            f"A visit that is {LABELS[ex.execution_status]} cannot become {LABELS[to]}."
        )
    old = ex.execution_status
    ex.execution_status = to
    ex.updated_at = utc_now()
    session.flush()
    record(session, "visit", ex.scheduled_visit_id, "status_changed", actor, **{"from": old.value, "to": to.value}, **details)


def get_execution(session: Session, visit_id: int) -> VisitExecution | None:
    return session.scalar(select(VisitExecution).where(VisitExecution.scheduled_visit_id == visit_id))


def ensure_execution(session: Session, visit: Visit) -> VisitExecution:
    ex = get_execution(session, visit.id)
    if ex is None:
        ex = VisitExecution(scheduled_visit_id=visit.id, caregiver_id=visit.caregiver_id,
                            execution_status=E.SCHEDULED, review_reasons=[])
        session.add(ex)
        session.flush()
    return ex


def create_scheduled_executions(session: Session, booking: Booking) -> int:
    """Called when a booking is confirmed (Week 2 workflow hook)."""
    n = 0
    for v in booking.visits:
        if v.assignment_status == VisitStatus.CONFIRMED and get_execution(session, v.id) is None:
            ensure_execution(session, v)
            n += 1
    return n


def cancel_scheduled_executions(session: Session, booking: Booking, actor: Actor) -> int:
    """Called when a booking is cancelled: visits not yet started become Cancelled."""
    n = 0
    for v in booking.visits:
        ex = get_execution(session, v.id)
        if ex and ex.execution_status == E.SCHEDULED:
            _transition(session, ex, E.CANCELLED, actor, reason="booking cancelled")
            n += 1
    return n


def _pet(visit: Visit) -> str:
    return visit.booking.pet.name


# -------------------------------------------------------------- check in/out
def check_in(session: Session, visit_id: int, actor: Actor, now: datetime | None = None) -> VisitExecution:
    visit = session.get(Visit, visit_id)
    if visit is None:
        raise ExecutionError("Visit not found.")
    if actor.role != "caregiver" or actor.id != visit.caregiver_id:
        raise AuthorizationError("Only the caregiver assigned to this visit can check in.")
    if visit.booking.status != BookingStatus.CONFIRMED or visit.assignment_status != VisitStatus.CONFIRMED:
        raise ExecutionError("Only visits of confirmed bookings can begin.")
    now = as_utc(now) or utc_now()
    tz = visit_tz(session, visit)
    if local_today(tz, now) != visit.visit_date:
        raise ExecutionError(f"This visit is scheduled for {visit.visit_date:%b %d}; check-in is only possible on that day.")
    ex = ensure_execution(session, visit)
    if ex.execution_status != E.SCHEDULED:
        raise InvalidVisitTransition(f"This visit is already {LABELS[ex.execution_status].lower()}; it cannot be started again.")
    ex.actual_check_in = now
    ex.caregiver_id = actor.id
    _transition(session, ex, E.IN_PROGRESS, actor, at=now.isoformat())
    b = visit.booking
    notify(session, b.resident_id, b.id, N.VISIT_STARTED, f"visit:{visit.id}",
           f"{_pet(visit)}'s visit started at {fmt_local(now, tz)}.",
           f"PawPal update: {_pet(visit)}'s care visit has started. Open the PawPal app for details.")
    return ex


@dataclass
class CheckOutResult:
    execution: VisitExecution
    needs_review: bool
    reasons: list[str]
    day_complete: bool


def check_out(session: Session, visit_id: int, actor: Actor, now: datetime | None = None) -> CheckOutResult:
    visit = session.get(Visit, visit_id)
    if visit is None:
        raise ExecutionError("Visit not found.")
    if actor.role != "caregiver" or actor.id != visit.caregiver_id:
        raise AuthorizationError("Only the caregiver assigned to this visit can check out.")
    ex = get_execution(session, visit_id)
    if ex is None or ex.execution_status == E.SCHEDULED or ex.actual_check_in is None:
        raise InvalidVisitTransition("You cannot check out before checking in.")
    if ex.execution_status != E.IN_PROGRESS:
        raise InvalidVisitTransition(f"This visit is already {LABELS[ex.execution_status].lower()}.")
    now = as_utc(now) or utc_now()
    if now <= as_utc(ex.actual_check_in):
        raise ExecutionError("Check-out must be after check-in.")
    ex.actual_check_out = now
    fill_unrecorded_essentials(session, visit)
    reasons = review_reasons(session, visit)
    ex.review_reasons = reasons
    _transition(session, ex, E.NEEDS_REVIEW if reasons else E.COMPLETED, actor, at=now.isoformat())
    tz = visit_tz(session, visit)
    b = visit.booking
    notify(session, b.resident_id, b.id, N.VISIT_COMPLETED, f"visit:{visit.id}",
           f"{_pet(visit)}'s visit ended at {fmt_local(now, tz)}. The daily report will follow after review.",
           f"PawPal update: {_pet(visit)}'s care visit is complete. Open the PawPal app for details.")
    return CheckOutResult(ex, bool(reasons), reasons, day_is_complete(session, b.id, visit.visit_date))


def day_is_complete(session: Session, booking_id: int, day: date) -> bool:
    visits = session.scalars(select(Visit).where(
        Visit.booking_id == booking_id, Visit.visit_date == day,
        Visit.assignment_status.in_([VisitStatus.CONFIRMED, VisitStatus.COMPLETED]))).all()
    if not visits:
        return False
    for v in visits:
        ex = get_execution(session, v.id)
        if ex is None or ex.execution_status not in TERMINAL:
            return False
    return True


# ------------------------------------------------------------ daily checks
@dataclass
class DailyCheckResult:
    missed: int
    unclosed: int
    completed_days: list[tuple[int, date]]


def run_daily_checks(session: Session, now: datetime | None = None, actor: Actor = SYSTEM) -> DailyCheckResult:
    """Mark visits whose local day has ended: never started -> Missed; never checked out -> Needs Review.

    Nothing is ever marked Completed here.
    """
    now = as_utc(now) or utc_now()
    rows = session.execute(
        select(VisitExecution, Visit).join(Visit, VisitExecution.scheduled_visit_id == Visit.id)
        .where(VisitExecution.execution_status.in_([E.SCHEDULED, E.IN_PROGRESS]))
    ).all()
    missed = unclosed = 0
    days: set[tuple[int, date]] = set()
    for ex, visit in rows:
        if visit.booking.status != BookingStatus.CONFIRMED and ex.execution_status == E.SCHEDULED:
            continue
        tz = visit_tz(session, visit)
        if local_today(tz, now) <= visit.visit_date:
            continue
        b = visit.booking
        if ex.execution_status == E.SCHEDULED:
            ex.review_reasons = ["No check-in was recorded for this visit."]
            _transition(session, ex, E.MISSED, actor)
            missed += 1
            notify(session, b.resident_id, b.id, N.MISSED_VISIT, f"visit:{visit.id}",
                   f"We have no record of {_pet(visit)}'s visit on {visit.visit_date:%b %d} "
                   f"({visit.start_time:%H:%M}). Our team has been alerted.",
                   f"PawPal: a scheduled visit for {_pet(visit)} was not recorded. Please open the PawPal app.")
        else:
            ex.review_reasons = list(ex.review_reasons or []) + ["No check-out was recorded."]
            _transition(session, ex, E.NEEDS_REVIEW, actor)
            unclosed += 1
        days.add((b.id, visit.visit_date))
    complete = sorted(d for d in days if day_is_complete(session, *d))
    return DailyCheckResult(missed, unclosed, complete)


def resolve_review(session: Session, visit_id: int, actor: Actor, note: str) -> VisitExecution:
    """A manager acknowledges a Needs Review / Missed visit. The status itself is kept."""
    ex = get_execution(session, visit_id)
    if ex is None or ex.execution_status not in (E.NEEDS_REVIEW, E.MISSED):
        raise ExecutionError("Only visits needing review or missed visits can be resolved.")
    manager = session.get(PropertyManager, actor.id) if actor.role == "property_manager" else None
    if manager is None or not manager.manages(ex.visit.booking.resident.building_name):
        raise AuthorizationError("Only a manager of this building can resolve visit issues.")
    if not (note or "").strip():
        raise ExecutionError("Please add a note describing how the issue was handled.")
    ex.reviewed_at, ex.reviewed_by, ex.review_note = utc_now(), actor.id, note.strip()
    session.flush()
    record(session, "visit", visit_id, "review_resolved", actor)
    return ex


# ----------------------------------------------------------------- queries
def is_overdue(session: Session, visit: Visit, ex: VisitExecution | None, now: datetime | None = None) -> bool:
    if ex is None or ex.execution_status != E.SCHEDULED:
        return False
    tz = visit_tz(session, visit)
    end = datetime.combine(visit.visit_date, visit.end_time, tzinfo=tz)
    return (as_utc(now) or utc_now()) > end + OVERDUE_AFTER


def caregiver_day(session: Session, caregiver_id: int, day: date) -> list[tuple[Visit, VisitExecution | None]]:
    visits = session.scalars(
        select(Visit).where(
            Visit.caregiver_id == caregiver_id, Visit.visit_date == day,
            Visit.assignment_status.in_([VisitStatus.CONFIRMED, VisitStatus.COMPLETED]),
        ).order_by(Visit.start_time)
    ).all()
    return [(v, get_execution(session, v.id)) for v in visits]
