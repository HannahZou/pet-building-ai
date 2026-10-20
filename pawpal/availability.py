"""Caregiver availability, time off and capabilities (database operations).

Rules
-----
* A caregiver may edit only their own records; a property manager may edit any.
* Working hours are blocks on specific dates (several per day allowed). A weekly
  template can fill a date range.
* Time off always wins over working hours.
* Hours cannot be removed, and time off cannot be taken, over a *confirmed*
  visit: the manager must reassign it first. Proposed (unconfirmed) visits are
  not blocked; they are flagged at the final confirmation check instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Iterable

from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from .actors import Actor, AuthorizationError
from .models import (
    Caregiver,
    CaregiverAvailability,
    CaregiverBuilding,
    CaregiverPetType,
    CaregiverService,
    CaregiverTimeOff,
    ServiceType,
    Visit,
    VisitStatus,
)

MAX_TEMPLATE_DAYS = 120


class AvailabilityError(Exception):
    pass


@dataclass(frozen=True)
class Block:
    start: time
    end: time

    def label(self) -> str:
        return f"{self.start:%H:%M}–{self.end:%H:%M}"


def _authorize(actor: Actor, caregiver_id: int) -> None:
    if actor.role == "property_manager":
        return
    if actor.role == "caregiver" and actor.id == caregiver_id:
        return
    raise AuthorizationError("You can only change your own availability.")


def _caregiver(session: Session, caregiver_id: int) -> Caregiver:
    cg = session.get(Caregiver, caregiver_id)
    if cg is None:
        raise AvailabilityError("Caregiver not found.")
    return cg


def validate_blocks(blocks: Iterable[Block]) -> list[Block]:
    ordered = sorted(blocks, key=lambda b: b.start)
    for b in ordered:
        if b.end <= b.start:
            raise AvailabilityError(f"Block {b.label()} ends before it starts.")
    for a, b in zip(ordered, ordered[1:]):
        if b.start < a.end:
            raise AvailabilityError(f"Blocks {a.label()} and {b.label()} overlap.")
    return ordered


def _confirmed_visits(session: Session, caregiver_id: int, start: date, end: date) -> list[Visit]:
    return list(
        session.scalars(
            select(Visit)
            .where(
                Visit.caregiver_id == caregiver_id,
                Visit.assignment_status == VisitStatus.CONFIRMED,
                Visit.visit_date.between(start, end),
            )
            .order_by(Visit.visit_date, Visit.start_time)
        )
    )


def _covered(blocks: list[Block], start: time, end: time) -> bool:
    return any(b.start <= start and end <= b.end for b in blocks)


# ------------------------------------------------------------- capabilities
def set_capabilities(
    session: Session,
    caregiver_id: int,
    actor: Actor,
    species: Iterable[str],
    services: Iterable[ServiceType],
    buildings: Iterable[str],
) -> Caregiver:
    _authorize(actor, caregiver_id)
    cg = _caregiver(session, caregiver_id)
    _sync(session, cg.pet_types, "species", {s.strip().lower() for s in species if s.strip()},
          lambda v: CaregiverPetType(species=v))
    _sync(session, cg.services, "service_type", {ServiceType(s) for s in services},
          lambda v: CaregiverService(service_type=v))
    _sync(session, cg.buildings, "building_name", {b for b in buildings if b},
          lambda v: CaregiverBuilding(building_name=v))
    session.flush()
    return cg


def _sync(session: Session, rows: list, attr: str, wanted: set, make) -> None:
    """Delete rows no longer wanted first, then add new ones (keeps unique keys valid)."""
    for row in [r for r in rows if getattr(r, attr) not in wanted]:
        rows.remove(row)
    session.flush()
    have = {getattr(r, attr) for r in rows}
    for value in sorted(wanted - have, key=str):
        rows.append(make(value))


# --------------------------------------------------------------- work hours
def blocks_on(session: Session, caregiver_id: int, work_date: date) -> list[Block]:
    rows = session.scalars(
        select(CaregiverAvailability)
        .where(CaregiverAvailability.caregiver_id == caregiver_id, CaregiverAvailability.work_date == work_date)
        .order_by(CaregiverAvailability.start_time)
    )
    return [Block(r.start_time, r.end_time) for r in rows]


def set_day_availability(
    session: Session, caregiver_id: int, work_date: date, blocks: Iterable[Block], actor: Actor
) -> list[Block]:
    """Replace the working hours for one date. An empty list = not working that day."""
    _authorize(actor, caregiver_id)
    _caregiver(session, caregiver_id)
    ordered = validate_blocks(blocks)
    for v in _confirmed_visits(session, caregiver_id, work_date, work_date):
        if not _covered(ordered, v.start_time, v.end_time):
            raise AvailabilityError(
                f"You have a confirmed visit {v.visit_date:%b %d} {v.start_time:%H:%M}–{v.end_time:%H:%M}. "
                "Ask a property manager to reassign it before changing these hours."
            )
    session.execute(
        delete(CaregiverAvailability).where(
            CaregiverAvailability.caregiver_id == caregiver_id,
            CaregiverAvailability.work_date == work_date,
        )
    )
    for b in ordered:
        session.add(CaregiverAvailability(caregiver_id=caregiver_id, work_date=work_date, start_time=b.start, end_time=b.end))
    session.flush()
    return ordered


def apply_weekly_template(
    session: Session,
    caregiver_id: int,
    actor: Actor,
    start: date,
    end: date,
    weekdays: Iterable[int],
    blocks: Iterable[Block],
) -> int:
    """Set the same hours on the chosen weekdays (Mon=0) in a date range.

    Days not in ``weekdays`` are left untouched. Returns the number of dates set.
    """
    if end < start:
        raise AvailabilityError("The end date is before the start date.")
    if (end - start).days + 1 > MAX_TEMPLATE_DAYS:
        raise AvailabilityError(f"Apply at most {MAX_TEMPLATE_DAYS} days at a time.")
    days = set(weekdays)
    ordered = validate_blocks(blocks)
    count = 0
    d = start
    while d <= end:
        if d.weekday() in days:
            set_day_availability(session, caregiver_id, d, ordered, actor)
            count += 1
        d += timedelta(days=1)
    return count


def availability_map(
    session: Session, caregiver_ids: Iterable[int], start: date, end: date
) -> dict[int, dict[date, list[Block]]]:
    ids = list(caregiver_ids)
    out: dict[int, dict[date, list[Block]]] = {cid: {} for cid in ids}
    if not ids:
        return out
    rows = session.scalars(
        select(CaregiverAvailability)
        .where(
            CaregiverAvailability.caregiver_id.in_(ids),
            CaregiverAvailability.work_date.between(start, end),
        )
        .order_by(CaregiverAvailability.start_time)
    )
    for r in rows:
        out[r.caregiver_id].setdefault(r.work_date, []).append(Block(r.start_time, r.end_time))
    return out


# ----------------------------------------------------------------- time off
def request_time_off(
    session: Session, caregiver_id: int, actor: Actor, start: date, end: date, reason: str | None = None
) -> CaregiverTimeOff:
    """Record time off (approved immediately in the MVP)."""
    _authorize(actor, caregiver_id)
    _caregiver(session, caregiver_id)
    if end < start:
        raise AvailabilityError("Time off must end on or after its start date.")
    clashes = _confirmed_visits(session, caregiver_id, start, end)
    if clashes:
        first = clashes[0]
        raise AvailabilityError(
            f"You have {len(clashes)} confirmed visit(s) in that period (first: {first.visit_date:%b %d} "
            f"{first.start_time:%H:%M}). A property manager must reassign them first."
        )
    row = CaregiverTimeOff(caregiver_id=caregiver_id, start_date=start, end_date=end, reason=(reason or "").strip() or None)
    session.add(row)
    session.flush()
    return row


def mark_day_off(session: Session, caregiver_id: int, actor: Actor, day: date, reason: str | None = None) -> CaregiverTimeOff:
    return request_time_off(session, caregiver_id, actor, day, day, reason or "Day off")


def cancel_time_off(session: Session, time_off_id: int, actor: Actor) -> None:
    row = session.get(CaregiverTimeOff, time_off_id)
    if row is None:
        raise AvailabilityError("Time off not found.")
    _authorize(actor, row.caregiver_id)
    session.delete(row)
    session.flush()


def time_off_map(
    session: Session, caregiver_ids: Iterable[int], start: date, end: date
) -> dict[int, list[CaregiverTimeOff]]:
    ids = list(caregiver_ids)
    out: dict[int, list[CaregiverTimeOff]] = {cid: [] for cid in ids}
    if not ids:
        return out
    rows = session.scalars(
        select(CaregiverTimeOff).where(
            CaregiverTimeOff.caregiver_id.in_(ids),
            and_(CaregiverTimeOff.start_date <= end, CaregiverTimeOff.end_date >= start),
        )
    )
    for r in rows:
        out[r.caregiver_id].append(r)
    return out


def is_off(time_off: list[CaregiverTimeOff], day: date) -> CaregiverTimeOff | None:
    return next((t for t in time_off if t.start_date <= day <= t.end_date), None)


def list_time_off(session: Session, caregiver_id: int, from_date: date | None = None) -> list[CaregiverTimeOff]:
    q = select(CaregiverTimeOff).where(CaregiverTimeOff.caregiver_id == caregiver_id)
    if from_date:
        q = q.where(CaregiverTimeOff.end_date >= from_date)
    return list(session.scalars(q.order_by(CaregiverTimeOff.start_date)))


# -------------------------------------------------------------------- visits
def caregiver_visits(
    session: Session, caregiver_id: int, start: date, end: date, include_proposed: bool = True
) -> list[Visit]:
    statuses = [VisitStatus.CONFIRMED, VisitStatus.COMPLETED]
    if include_proposed:
        statuses.append(VisitStatus.PROPOSED)
    return list(
        session.scalars(
            select(Visit)
            .where(
                Visit.caregiver_id == caregiver_id,
                Visit.assignment_status.in_(statuses),
                Visit.visit_date.between(start, end),
            )
            .order_by(Visit.visit_date, Visit.start_time)
        )
    )


def list_caregivers(session: Session, active_only: bool = False) -> list[Caregiver]:
    q = select(Caregiver).order_by(Caregiver.full_name)
    if active_only:
        q = q.where(Caregiver.active_status.is_(True))
    return list(session.scalars(q))
