"""Read-only data loading for analytics (one row per booking / visit / caregiver).

Rows are plain dataclasses built from the database in a few queries; metric code
never touches the ORM. Each booking appears exactly once, however many visits it
has, so booking metrics cannot double count.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..access import Scope
from ..models import (
    AuditEvent,
    Booking,
    BookingEvent,
    BookingQuote,
    BookingStatus as S,
    Caregiver,
    CaregiverAvailability,
    CaregiverBuilding,
    CaregiverTimeOff,
    DailyCareReport,
    ExecutionStatus as E,
    Notification,
    NotificationChannel,
    Resident,
    ServiceType,
    Visit,
    VisitExecution,
    VisitStatus,
)
from ..timeutil import as_utc, building_tz

DataSource = Literal["synthetic", "real"]


@dataclass(frozen=True)
class AnalyticsFilter:
    start: date
    end: date
    buildings: tuple[str, ...] | None = None          # narrowed further by the actor's scope
    service_types: tuple[ServiceType, ...] | None = None
    statuses: tuple[S, ...] | None = None
    data_source: DataSource = "synthetic"             # never mixes synthetic and real records

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def previous(self) -> "AnalyticsFilter":
        span = timedelta(days=self.days)
        return AnalyticsFilter(self.start - span, self.start - timedelta(days=1), self.buildings,
                               self.service_types, self.statuses, self.data_source)


@dataclass
class BookingRow:
    id: int
    resident_id: int
    building: str
    service: ServiceType
    status: S
    start_date: date
    end_date: date
    created_at: datetime
    total_cents: int | None
    confirmed_at: datetime | None
    assigned_at: datetime | None
    scheduling_attempts: int
    scheduling_conflicts: int
    reassignments: int


@dataclass
class VisitRow:
    id: int
    booking_id: int
    building: str
    service: ServiceType
    caregiver_id: int | None
    visit_date: date
    start_time: time
    end_time: time
    minutes: int
    assignment: VisitStatus
    execution: E | None
    check_in: datetime | None
    check_out: datetime | None
    tz: object = None

    @property
    def scheduled_start(self) -> datetime:
        return datetime.combine(self.visit_date, self.start_time, tzinfo=self.tz)


@dataclass
class Dataset:
    filter: AnalyticsFilter
    scope: Scope
    bookings: list[BookingRow] = field(default_factory=list)
    visits: list[VisitRow] = field(default_factory=list)
    as_of: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _naive_utc(dt: datetime | None) -> datetime | None:
    return as_utc(dt).replace(tzinfo=None) if dt else None


def _scoped_residents(session: Session, scope: Scope, f: AnalyticsFilter) -> dict[int, Resident]:
    q = select(Resident).where(Resident.is_synthetic.is_(f.data_source == "synthetic"))
    rows = session.scalars(q).all()
    wanted = set(f.buildings) if f.buildings else None
    return {r.id: r for r in rows if scope.allows(r.building_name) and (wanted is None or r.building_name in wanted)}


def load_bookings(session: Session, scope: Scope, f: AnalyticsFilter, window: str = "start") -> list[BookingRow]:
    """Bookings in scope. ``window='start'`` filters by service start date, ``'created'`` by request date."""
    residents = _scoped_residents(session, scope, f)
    if not residents:
        return []
    q = select(Booking).where(Booking.resident_id.in_(residents))
    if f.service_types:
        q = q.where(Booking.service_type.in_(f.service_types))
    if f.statuses:
        q = q.where(Booking.status.in_(f.statuses))
    bookings = session.scalars(q).all()
    ids = [b.id for b in bookings]
    quotes = {q.booking_id: q.total_cents for q in session.scalars(
        select(BookingQuote).where(BookingQuote.booking_id.in_(ids), BookingQuote.is_current.is_(True)))}
    events: dict[int, list[BookingEvent]] = defaultdict(list)
    for ev in session.scalars(select(BookingEvent).where(BookingEvent.booking_id.in_(ids)).order_by(BookingEvent.id)):
        events[ev.booking_id].append(ev)

    rows = []
    for b in bookings:
        created = _naive_utc(b.created_at)
        key_date = b.start_date if window == "start" else created.date()
        if not (f.start <= key_date <= f.end):
            continue
        evs = events.get(b.id, [])
        confirmed = next((e.created_at for e in evs if e.to_status == S.CONFIRMED.value), None)
        assigned = next((e.created_at for e in evs if e.to_status == S.PENDING_RESIDENT_APPROVAL.value), None)
        # a scheduling attempt = the matcher ran (from Quoted) or the final confirmation check ran
        attempts = [e for e in evs if (e.from_status == S.QUOTED.value and e.to_status in
                                       (S.PENDING_RESIDENT_APPROVAL.value, S.NEEDS_RESCHEDULING.value))
                    or (e.from_status == S.PENDING_ADMIN_APPROVAL.value and e.to_status in
                        (S.CONFIRMED.value, S.NEEDS_RESCHEDULING.value))]
        rows.append(BookingRow(
            id=b.id, resident_id=b.resident_id, building=residents[b.resident_id].building_name,
            service=b.service_type, status=b.status, start_date=b.start_date, end_date=b.end_date,
            created_at=created, total_cents=quotes.get(b.id),
            confirmed_at=_naive_utc(confirmed), assigned_at=_naive_utc(assigned),
            scheduling_attempts=len(attempts),
            scheduling_conflicts=sum(1 for e in attempts if e.to_status == S.NEEDS_RESCHEDULING.value),
            reassignments=sum(1 for e in evs if e.from_status == e.to_status and "reassigned" in (e.note or "")),
        ))
    return rows


def load_visits(session: Session, scope: Scope, f: AnalyticsFilter) -> list[VisitRow]:
    """Visits of confirmed/completed bookings in scope, by visit date."""
    residents = _scoped_residents(session, scope, f)
    if not residents:
        return []
    q = (
        select(Visit, Booking)
        .join(Booking, Visit.booking_id == Booking.id)
        .where(Booking.resident_id.in_(residents), Visit.visit_date.between(f.start, f.end),
               Booking.status.in_([S.CONFIRMED, S.COMPLETED, S.CANCELLED]),
               Visit.assignment_status.in_([VisitStatus.CONFIRMED, VisitStatus.COMPLETED]))
    )
    if f.service_types:
        q = q.where(Booking.service_type.in_(f.service_types))
    if f.statuses:
        q = q.where(Booking.status.in_(f.statuses))
    pairs = session.execute(q).all()
    execs = {e.scheduled_visit_id: e for e in session.scalars(
        select(VisitExecution).where(VisitExecution.scheduled_visit_id.in_([v.id for v, _ in pairs])))}
    tzs: dict[str, object] = {}
    rows = []
    for v, b in pairs:
        building = residents[b.resident_id].building_name
        tz = tzs.setdefault(building, building_tz(session, building))
        ex = execs.get(v.id)
        rows.append(VisitRow(
            id=v.id, booking_id=b.id, building=building, service=b.service_type, caregiver_id=v.caregiver_id,
            visit_date=v.visit_date, start_time=v.start_time, end_time=v.end_time, minutes=v.duration_minutes,
            assignment=v.assignment_status, execution=ex.execution_status if ex else None,
            check_in=as_utc(ex.actual_check_in) if ex else None, check_out=as_utc(ex.actual_check_out) if ex else None,
            tz=tz,
        ))
    return rows


def caregiver_capacity(session: Session, scope: Scope, f: AnalyticsFilter) -> dict[int, dict]:
    """Available minutes per active caregiver who covers a building in scope (time off removed)."""
    covers: dict[int, set[str]] = defaultdict(set)
    for row in session.scalars(select(CaregiverBuilding)):
        covers[row.caregiver_id].add(row.building_name)
    wanted = set(f.buildings) if f.buildings else None
    caregivers = [c for c in session.scalars(select(Caregiver).where(Caregiver.active_status.is_(True)))
                  if any(scope.allows(b) and (wanted is None or b in wanted) for b in covers.get(c.id, ()))]
    ids = [c.id for c in caregivers]
    off_days: dict[int, set[date]] = defaultdict(set)
    for t in session.scalars(select(CaregiverTimeOff).where(CaregiverTimeOff.caregiver_id.in_(ids))):
        d = max(t.start_date, f.start)
        while d <= min(t.end_date, f.end):
            off_days[t.caregiver_id].add(d)
            d += timedelta(days=1)
    minutes: dict[int, int] = defaultdict(int)
    for a in session.scalars(select(CaregiverAvailability).where(
            CaregiverAvailability.caregiver_id.in_(ids), CaregiverAvailability.work_date.between(f.start, f.end))):
        if a.work_date in off_days[a.caregiver_id]:
            continue
        minutes[a.caregiver_id] += int((datetime.combine(a.work_date, a.end_time) -
                                        datetime.combine(a.work_date, a.start_time)).total_seconds() // 60)
    return {c.id: {"name": c.full_name, "available_minutes": minutes.get(c.id, 0)} for c in caregivers}


def caregiver_assigned_minutes(session: Session, caregiver_ids: list[int], f: AnalyticsFilter) -> dict[int, int]:
    """All assigned (confirmed/completed) minutes of these caregivers in the window, any building,
    restricted to the selected data source."""
    synthetic = f.data_source == "synthetic"
    q = (
        select(Visit.caregiver_id, Visit.duration_minutes)
        .join(Booking, Visit.booking_id == Booking.id)
        .join(Resident, Booking.resident_id == Resident.id)
        .where(Visit.caregiver_id.in_(caregiver_ids), Visit.visit_date.between(f.start, f.end),
               Visit.assignment_status.in_([VisitStatus.CONFIRMED, VisitStatus.COMPLETED]),
               Resident.is_synthetic.is_(synthetic))
    )
    out: dict[int, int] = defaultdict(int)
    for cid, mins in session.execute(q).all():
        out[cid] += mins
    return out


def load_reports(session: Session, scope: Scope, f: AnalyticsFilter) -> list[DailyCareReport]:
    residents = _scoped_residents(session, scope, f)
    if not residents:
        return []
    return list(session.scalars(
        select(DailyCareReport).join(Booking, DailyCareReport.booking_id == Booking.id)
        .where(Booking.resident_id.in_(residents), DailyCareReport.report_date.between(f.start, f.end))
    ))


def rejected_report_ids(session: Session, report_ids: list[int]) -> set[int]:
    return set(session.scalars(select(AuditEvent.entity_id).where(
        AuditEvent.entity_type == "daily_report", AuditEvent.action == "rejected",
        AuditEvent.entity_id.in_(report_ids))))


def last_checkout(session: Session, booking_id: int, day: date) -> datetime | None:
    rows = session.scalars(select(VisitExecution.actual_check_out).join(Visit, VisitExecution.scheduled_visit_id == Visit.id)
                           .where(Visit.booking_id == booking_id, Visit.visit_date == day)).all()
    stamps = [as_utc(r) for r in rows if r]
    return max(stamps) if stamps else None


def load_email_notifications(session: Session, scope: Scope, f: AnalyticsFilter) -> list[Notification]:
    residents = _scoped_residents(session, scope, f)
    if not residents:
        return []
    start = datetime.combine(f.start, time.min)
    end = datetime.combine(f.end + timedelta(days=1), time.min)
    rows = session.scalars(select(Notification).where(
        Notification.resident_id.in_(residents), Notification.channel == NotificationChannel.EMAIL)).all()
    return [n for n in rows if start <= _naive_utc(n.created_at) < end]
