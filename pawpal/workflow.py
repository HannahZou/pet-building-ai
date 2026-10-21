"""Booking approval workflow (state machine, authorisation, confirmation).

    Draft -> Quoted -> Pending Resident Approval -> Pending Admin Approval -> Confirmed -> Completed
                  \\-> Needs Rescheduling (no caregiver / conflict found) --^
    Rejected / Cancelled are terminal.

* Residents approve the quoted price and proposed schedule of their own bookings.
* Property managers approve caregiver assignments for buildings they manage.
* ``confirm_booking`` is the only way to reach Confirmed. It runs in a
  write-locked transaction (``BEGIN IMMEDIATE`` on SQLite), re-checks every visit
  against the database, and marks the visits confirmed; a database trigger
  rejects any overlapping confirmed visit as a last line of defence. The booking
  row also carries an optimistic-lock ``version``.
* Every status change is written to ``booking_events`` with actor role, id and time.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Iterable

from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from .actors import SYSTEM, Actor, AuthorizationError
from .database import session_scope, write_locked_session
from .models import (
    ALLOWED_VISIT_MINUTES,
    Booking,
    BookingEvent,
    BookingQuote,
    BookingStatus as S,
    PropertyManager,
    VisitStatus,
)
from .pricing import PriceBook, PricingError, active_price_book, quote_for_booking, save_quote
from .visit_execution import cancel_scheduled_executions, create_scheduled_executions
from .scheduling import (
    Conflict,
    Issue,
    Recommendation,
    SchedulingError,
    Window,
    apply_recommendation,
    check_booking_schedule,
    generate_visits,
    recommend_schedule,
    set_time_windows,
    validate_assignment,
)

logger = logging.getLogger(__name__)

TRANSITIONS: dict[S, set[S]] = {
    S.DRAFT: {S.QUOTED, S.CANCELLED},
    S.QUOTED: {S.QUOTED, S.PENDING_RESIDENT_APPROVAL, S.NEEDS_RESCHEDULING, S.REJECTED, S.CANCELLED},
    S.NEEDS_RESCHEDULING: {S.QUOTED, S.PENDING_RESIDENT_APPROVAL, S.REJECTED, S.CANCELLED},
    S.PENDING_RESIDENT_APPROVAL: {S.QUOTED, S.PENDING_ADMIN_APPROVAL, S.NEEDS_RESCHEDULING, S.REJECTED, S.CANCELLED},
    S.PENDING_ADMIN_APPROVAL: {S.QUOTED, S.CONFIRMED, S.NEEDS_RESCHEDULING, S.REJECTED, S.CANCELLED},
    S.CONFIRMED: {S.COMPLETED, S.CANCELLED},
    S.COMPLETED: set(),
    S.CANCELLED: set(),
    S.REJECTED: set(),
}
PREPARABLE = {S.DRAFT, S.QUOTED, S.NEEDS_RESCHEDULING, S.PENDING_RESIDENT_APPROVAL, S.PENDING_ADMIN_APPROVAL}
TERMINAL = {S.COMPLETED, S.CANCELLED, S.REJECTED}

STATUS_LABELS = {
    S.DRAFT: "Draft",
    S.QUOTED: "Quoted",
    S.PENDING_RESIDENT_APPROVAL: "Pending Resident Approval",
    S.PENDING_ADMIN_APPROVAL: "Pending Admin Approval",
    S.CONFIRMED: "Confirmed",
    S.COMPLETED: "Completed",
    S.CANCELLED: "Cancelled",
    S.REJECTED: "Rejected",
    S.NEEDS_RESCHEDULING: "Needs Rescheduling",
}


class WorkflowError(Exception):
    pass


class InvalidTransition(WorkflowError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ helpers
def _booking(session: Session, booking_id: int) -> Booking:
    booking = session.get(Booking, booking_id)
    if booking is None:
        raise WorkflowError("Booking not found.")
    return booking


def transition(session: Session, booking: Booking, to: S, actor: Actor, note: str | None = None) -> None:
    current = booking.status
    if to not in TRANSITIONS[current]:
        raise InvalidTransition(f"A booking cannot move from {STATUS_LABELS[current]} to {STATUS_LABELS[to]}.")
    booking.status = to
    booking.events.append(
        BookingEvent(from_status=current.value, to_status=to.value, actor_role=actor.role, actor_id=actor.id, note=note)
    )
    session.flush()
    logger.info("Booking %s: %s -> %s by %s#%s", booking.id, current.value, to.value, actor.role, actor.id)


def _require_resident(booking: Booking, actor: Actor) -> None:
    if actor.role != "resident" or actor.id != booking.resident_id:
        raise AuthorizationError("Only the resident who made this booking can do that.")


def _require_manager(session: Session, booking: Booking, actor: Actor) -> PropertyManager:
    if actor.role != "property_manager":
        raise AuthorizationError("Only a property manager can approve or change caregiver assignments.")
    manager = session.get(PropertyManager, actor.id)
    if manager is None:
        raise AuthorizationError("Unknown property manager.")
    if not manager.manages(booking.resident.building_name):
        raise AuthorizationError(f"{manager.full_name} does not manage {booking.resident.building_name}.")
    return manager


def _require_resident_or_manager(session: Session, booking: Booking, actor: Actor) -> None:
    if actor.role == "resident":
        _require_resident(booking, actor)
    else:
        _require_manager(session, booking, actor)


def _clear_approvals(booking: Booking) -> None:
    booking.resident_approved_at = booking.resident_approved_by = None
    booking.admin_approved_at = booking.admin_approved_by = None


def _release_visits(booking: Booking) -> None:
    from .models import ExecutionStatus, VisitExecution

    session = Session.object_session(booking)
    for v in booking.visits:
        if v.assignment_status == VisitStatus.COMPLETED:
            continue
        ex = session.scalar(select(VisitExecution).where(VisitExecution.scheduled_visit_id == v.id)) if session else None
        if ex is not None and ex.execution_status not in (ExecutionStatus.SCHEDULED, ExecutionStatus.CANCELLED):
            continue  # already started/done: keep the record of what happened
        v.assignment_status = VisitStatus.CANCELLED


# ---------------------------------------------------- quote + schedule step
@dataclass
class PrepareResult:
    booking_id: int
    status: S
    quote: BookingQuote
    recommendation: Recommendation


def prepare_booking(
    session: Session,
    booking_id: int,
    actor: Actor,
    *,
    windows: list[Window] | None = None,
    duration_minutes: int | None = None,
    additional_services: Iterable[str] | None = None,
    book: PriceBook | None = None,
) -> PrepareResult:
    """Generate visits, price them and recommend caregivers.

    Also the "recalculate" path: changing duration, add-ons or windows re-runs
    everything, issues a new quote and clears earlier approvals.
    Raises ``MissingTimeWindowsError`` instead of inventing visit times.
    """
    booking = _booking(session, booking_id)
    _require_resident_or_manager(session, booking, actor)
    if booking.status not in PREPARABLE:
        raise InvalidTransition(f"A {STATUS_LABELS[booking.status]} booking cannot be re-quoted.")
    book = book or active_price_book()

    if duration_minutes is not None:
        if duration_minutes not in ALLOWED_VISIT_MINUTES:
            raise WorkflowError("Visits must be 30 or 60 minutes.")
        booking.visit_duration_minutes = duration_minutes
    if additional_services is not None:
        codes = sorted(set(additional_services))
        unknown = [c for c in codes if c not in book.additional_services]
        if unknown:
            raise PricingError(f"Unknown additional service: {', '.join(unknown)}.")
        booking.additional_services = codes
    if windows is not None:
        set_time_windows(session, booking, windows)

    generate_visits(session, booking)
    quote = save_quote(session, booking, quote_for_booking(booking, book))
    _clear_approvals(booking)
    was = booking.status
    transition(session, booking, S.QUOTED, actor, f"Quote {quote.pricing_version}: ${quote.total_cents / 100:,.2f}"
               + (" (recalculated)" if was != S.DRAFT else ""))

    rec = recommend_schedule(session, booking)
    apply_recommendation(session, booking, rec)
    if rec.fully_assigned:
        transition(session, booking, S.PENDING_RESIDENT_APPROVAL, SYSTEM, "All visits have a proposed caregiver.")
    else:
        transition(session, booking, S.NEEDS_RESCHEDULING, SYSTEM,
                   f"{len(rec.unassigned)} visit(s) have no available caregiver.")
    return PrepareResult(booking.id, booking.status, quote, rec)


def accept_alternative(
    session: Session, booking_id: int, visit_id: int, caregiver_id: int, start: time, actor: Actor
) -> Booking:
    """Resident (or manager) accepts a suggested alternative time for one visit."""
    booking = _booking(session, booking_id)
    _require_resident_or_manager(session, booking, actor)
    if booking.status not in (S.NEEDS_RESCHEDULING, S.PENDING_RESIDENT_APPROVAL):
        raise InvalidTransition("Alternatives can only be accepted before the resident approves.")
    visit = next((v for v in booking.visits if v.id == visit_id), None)
    if visit is None:
        raise WorkflowError("Visit not found on this booking.")
    res = validate_assignment(session, booking, visit, caregiver_id, start=start)
    if isinstance(res, Issue):
        raise SchedulingError(f"That time is no longer available: {res.message}")
    visit.caregiver_id = caregiver_id
    visit.start_time, visit.end_time = res
    visit.assignment_status = VisitStatus.PROPOSED
    session.flush()
    if booking.status == S.NEEDS_RESCHEDULING and all(v.caregiver_id for v in booking.visits):
        transition(session, booking, S.PENDING_RESIDENT_APPROVAL, actor, "Alternative times accepted.")
    return booking


# ----------------------------------------------------------- resident step
def resident_approve(session: Session, booking_id: int, actor: Actor) -> Booking:
    booking = _booking(session, booking_id)
    _require_resident(booking, actor)
    if booking.status != S.PENDING_RESIDENT_APPROVAL:
        raise InvalidTransition(f"Only bookings pending your approval can be approved (this one is {STATUS_LABELS[booking.status]}).")
    if booking.current_quote is None:
        raise WorkflowError("There is no current quote to approve.")
    if not booking.visits or any(v.caregiver_id is None for v in booking.visits):
        raise WorkflowError("Every visit needs a proposed caregiver before approval.")
    booking.resident_approved_at = _now()
    booking.resident_approved_by = actor.id
    transition(session, booking, S.PENDING_ADMIN_APPROVAL, actor,
               f"Resident approved quote ${booking.current_quote.total_cents / 100:,.2f} and schedule.")
    return booking


def resident_reject(session: Session, booking_id: int, actor: Actor, reason: str | None = None) -> Booking:
    booking = _booking(session, booking_id)
    _require_resident(booking, actor)
    if booking.status not in (S.QUOTED, S.PENDING_RESIDENT_APPROVAL, S.NEEDS_RESCHEDULING):
        raise InvalidTransition("This booking can no longer be rejected; cancel it instead.")
    _release_visits(booking)
    transition(session, booking, S.REJECTED, actor, reason or "Resident rejected the quote/schedule.")
    return booking


def cancel_booking(session: Session, booking_id: int, actor: Actor, reason: str | None = None) -> Booking:
    booking = _booking(session, booking_id)
    _require_resident_or_manager(session, booking, actor)
    if booking.status in TERMINAL:
        raise InvalidTransition(f"This booking is already {STATUS_LABELS[booking.status]}.")
    cancel_scheduled_executions(session, booking, actor)  # Week 3: not-yet-started visits
    _release_visits(booking)
    transition(session, booking, S.CANCELLED, actor, reason or "Cancelled.")
    return booking


# ------------------------------------------------------------ manager step
def reassign_visits(
    session: Session, booking_id: int, visit_ids: Iterable[int], caregiver_id: int, actor: Actor
) -> Booking:
    """Manager manually assigns visits to a caregiver (validated against the DB)."""
    booking = _booking(session, booking_id)
    manager = _require_manager(session, booking, actor)
    if booking.status not in (S.PENDING_ADMIN_APPROVAL, S.PENDING_RESIDENT_APPROVAL, S.NEEDS_RESCHEDULING):
        raise InvalidTransition(f"Visits of a {STATUS_LABELS[booking.status]} booking cannot be reassigned.")
    wanted = set(visit_ids)
    visits = [v for v in booking.visits if v.id in wanted]
    if not visits:
        raise WorkflowError("Choose at least one visit to reassign.")
    planned: list[tuple] = []
    for v in sorted(visits, key=lambda v: (v.visit_date, v.window_start)):
        res = validate_assignment(session, booking, v, caregiver_id)
        if isinstance(res, Issue):
            raise SchedulingError(f"{v.visit_date:%b %d} visit {v.slot}: {res.message}")
        v.caregiver_id = caregiver_id
        v.start_time, v.end_time = res
        v.assignment_status = VisitStatus.PROPOSED
        session.flush()  # so the next visit sees this one
        planned.append(res)
    booking.events.append(
        BookingEvent(from_status=booking.status.value, to_status=booking.status.value,
                     actor_role=actor.role, actor_id=actor.id,
                     note=f"{manager.full_name} reassigned {len(visits)} visit(s) to caregiver #{caregiver_id}.")
    )
    if booking.status == S.NEEDS_RESCHEDULING and all(v.caregiver_id for v in booking.visits):
        transition(session, booking, S.PENDING_RESIDENT_APPROVAL, actor, "All visits assigned by manager.")
    session.flush()
    return booking


def admin_reject(session: Session, booking_id: int, actor: Actor, reason: str | None = None) -> Booking:
    booking = _booking(session, booking_id)
    _require_manager(session, booking, actor)
    if booking.status not in (S.PENDING_ADMIN_APPROVAL, S.NEEDS_RESCHEDULING, S.PENDING_RESIDENT_APPROVAL, S.QUOTED):
        raise InvalidTransition(f"A {STATUS_LABELS[booking.status]} booking cannot be rejected.")
    _release_visits(booking)
    transition(session, booking, S.REJECTED, actor, reason or "Rejected by property manager.")
    return booking


def mark_completed(session: Session, booking_id: int, actor: Actor, today: date | None = None) -> Booking:
    booking = _booking(session, booking_id)
    _require_manager(session, booking, actor)
    today = today or date.today()
    if booking.status != S.CONFIRMED:
        raise InvalidTransition("Only confirmed bookings can be completed.")
    if booking.end_date >= today:
        raise WorkflowError("A booking can be completed after its last day.")
    for v in booking.visits:
        if v.assignment_status == VisitStatus.CONFIRMED:
            v.assignment_status = VisitStatus.COMPLETED
    transition(session, booking, S.COMPLETED, actor)
    return booking


@dataclass
class ConfirmResult:
    ok: bool
    status: S
    message: str
    conflicts: list[Conflict] = field(default_factory=list)


def confirm_booking(booking_id: int, actor: Actor, engine: Engine | None = None) -> ConfirmResult:
    """Admin approval + final checks + confirmation, atomically.

    Confirmed only if: resident approved, a manager of the building approves,
    every visit has a caregiver, and the final availability/conflict check passes.
    Otherwise the booking moves to Needs Rescheduling (with reasons).
    """
    try:
        with write_locked_session(engine) as s:
            booking = _booking(s, booking_id)
            _require_manager(s, booking, actor)
            if booking.status != S.PENDING_ADMIN_APPROVAL:
                raise InvalidTransition(
                    f"Only bookings pending admin approval can be confirmed (this one is {STATUS_LABELS[booking.status]})."
                )
            if not booking.resident_approved_at:
                raise WorkflowError("The resident has not approved this booking.")
            conflicts = check_booking_schedule(s, booking)
            if conflicts:
                summary = "; ".join(c.message for c in conflicts[:3])
                transition(s, booking, S.NEEDS_RESCHEDULING, actor, f"Final check failed: {summary}")
                return ConfirmResult(False, S.NEEDS_RESCHEDULING,
                                     f"Final check found {len(conflicts)} problem(s); moved to Needs Rescheduling.", conflicts)
            booking.admin_approved_at = _now()
            booking.admin_approved_by = actor.id
            for v in booking.visits:
                v.assignment_status = VisitStatus.CONFIRMED
            s.flush()  # DB trigger re-checks overlaps here
            transition(s, booking, S.CONFIRMED, actor, "Assignments approved; all checks passed.")
            create_scheduled_executions(s, booking)  # Week 3: visits ready for check-in
            return ConfirmResult(True, S.CONFIRMED, "Booking confirmed.")
    except IntegrityError as exc:
        if "caregiver_double_booked" not in str(exc):
            raise
        with session_scope(engine) as s:
            booking = _booking(s, booking_id)
            if booking.status == S.PENDING_ADMIN_APPROVAL:
                transition(s, booking, S.NEEDS_RESCHEDULING, actor,
                           "Database rejected an overlapping caregiver assignment.")
        return ConfirmResult(False, S.NEEDS_RESCHEDULING,
                             "Another booking was just confirmed for the same caregiver and time. Please reschedule.")
    except StaleDataError:
        return ConfirmResult(False, S.PENDING_ADMIN_APPROVAL,
                             "This booking was changed by someone else. Reload and try again.")


# ------------------------------------------------------------------ queries
def booking_rows(session: Session, statuses: Iterable[S] | None = None,
                 building: str | None = None, resident_id: int | None = None) -> list[dict]:
    from .models import Pet, Resident

    q = (
        select(Booking)
        .join(Resident, Booking.resident_id == Resident.id)
        .order_by(Booking.start_date.desc(), Booking.id.desc())
    )
    if statuses is not None:
        q = q.where(Booking.status.in_(list(statuses)))
    if building:
        q = q.where(Resident.building_name == building)
    if resident_id is not None:
        q = q.where(Booking.resident_id == resident_id)
    rows = []
    for b in session.scalars(q):
        quote = b.current_quote
        active = [v for v in b.visits if v.assignment_status != VisitStatus.CANCELLED]
        rows.append({
            "ID": b.id,
            "Resident": b.resident.full_name,
            "Building": b.resident.building_name,
            "Pet": f"{b.pet.name} ({b.pet.species})",
            "Dates": f"{b.start_date:%b %d} – {b.end_date:%b %d, %Y}",
            "Start": b.start_date,
            "Visits": len(active) or b.num_days * b.visits_per_day,
            "Unassigned": sum(1 for v in active if v.caregiver_id is None),
            "Total": f"${quote.total_cents / 100:,.2f}" if quote else "—",
            "Status": STATUS_LABELS[b.status],
        })
    return rows


def list_managers(session: Session) -> list[PropertyManager]:
    return list(session.scalars(select(PropertyManager).order_by(PropertyManager.full_name)))
