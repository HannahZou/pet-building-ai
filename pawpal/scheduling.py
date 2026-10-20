"""Deterministic scheduling engine: visits, conflicts and caregiver matching.

Nothing here calls an LLM. Every recommendation is computed from the database
and can be re-validated at any time (``check_booking_schedule``).

Matching rules
--------------
A caregiver can take a visit only if they are active, care for the pet's species,
offer the service, cover the resident's building, are not on time off, have a
working-hours block that contains the whole visit, and the visit does not
overlap anything else they hold (other bookings' confirmed or proposed visits,
or earlier visits in the same plan) once a travel buffer is added:
``TRAVEL_BUFFER_SAME_BUILDING`` between visits in one building, otherwise
``TRAVEL_BUFFER_OTHER_BUILDING``.

Ranking (MVP: minimise travel and operating cost): caregivers who can cover the
most visits of this booking first (continuity), then those who already have the
most visits in the same building on these dates (consolidation), then the
lightest overall load, then id (stable tie-break). Each visit goes to the
highest-ranked caregiver who can take it, at the earliest start inside the
resident's window (15-minute steps).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Iterable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .availability import Block, availability_map, is_off, time_off_map
from .models import (
    ALLOWED_VISIT_MINUTES,
    Booking,
    BookingStatus,
    BookingTimeWindow,
    Caregiver,
    Resident,
    ServiceType,
    Visit,
    VisitStatus,
)
from .schemas import SERVICE_LABELS

TRAVEL_BUFFER_SAME_BUILDING = timedelta(minutes=10)
TRAVEL_BUFFER_OTHER_BUILDING = timedelta(minutes=30)
START_STEP = timedelta(minutes=15)
MAX_ALTERNATIVES = 3

# Bookings whose *proposed* visits still hold a caregiver's time (soft holds).
ACTIVE_PIPELINE = (
    BookingStatus.QUOTED,
    BookingStatus.PENDING_RESIDENT_APPROVAL,
    BookingStatus.PENDING_ADMIN_APPROVAL,
    BookingStatus.NEEDS_RESCHEDULING,
)

PRESET_WINDOWS: dict[str, tuple[time, time]] = {
    "Early morning (6–8 AM)": (time(6), time(8)),
    "Morning (7–10 AM)": (time(7), time(10)),
    "Midday (11 AM–2 PM)": (time(11), time(14)),
    "Afternoon (2–5 PM)": (time(14), time(17)),
    "Evening (5–8 PM)": (time(17), time(20)),
    "Late evening (8–10 PM)": (time(20), time(22)),
}


class SchedulingError(Exception):
    pass


class MissingTimeWindowsError(SchedulingError):
    """The resident has not given a preferred time window for every daily visit."""

    def __init__(self, visits_per_day: int, have: int):
        self.questions = [
            f"Which time window works for visit {i} of each day?" for i in range(have + 1, visits_per_day + 1)
        ]
        super().__init__(
            f"Please choose a preferred time window for each of the {visits_per_day} daily visits "
            f"({have} provided). We never invent visit times."
        )


# ================================================================ time windows
@dataclass(frozen=True)
class Window:
    start: time
    end: time

    def label(self) -> str:
        return f"{self.start:%H:%M}–{self.end:%H:%M}"


def _dt(day: date, t: time) -> datetime:
    return datetime.combine(day, t)


def _minutes(a: time, b: time) -> int:
    return int((_dt(date.min, b) - _dt(date.min, a)).total_seconds() // 60)


def _add(t: time, delta: timedelta) -> time | None:
    """t + delta, or None if it crosses midnight (visits never span midnight)."""
    out = _dt(date(2000, 1, 1), t) + delta
    return out.time() if out.date() == date(2000, 1, 1) else None


def validate_windows(windows: list[Window], visits_per_day: int, duration_minutes: int) -> list[str]:
    errors: list[str] = []
    if duration_minutes not in ALLOWED_VISIT_MINUTES:
        errors.append("Visits must be 30 or 60 minutes.")
    if len(windows) != visits_per_day:
        errors.append(f"Choose exactly {visits_per_day} time window(s), one per daily visit.")
    for i, w in enumerate(windows, start=1):
        if w.end <= w.start:
            errors.append(f"Window {i} ({w.label()}) ends before it starts.")
        elif _minutes(w.start, w.end) < duration_minutes:
            errors.append(f"Window {i} ({w.label()}) is shorter than a {duration_minutes}-minute visit.")
    ordered = sorted(windows, key=lambda w: w.start)
    if ordered != list(windows):
        errors.append("List the time windows in order through the day.")
    for a, b in zip(ordered, ordered[1:]):
        if b.start < a.end:
            errors.append(f"Windows {a.label()} and {b.label()} overlap; each visit needs its own window.")
    return errors


def booking_windows(booking: Booking) -> list[Window]:
    return [Window(w.start_time, w.end_time) for w in sorted(booking.time_windows, key=lambda w: w.slot)]


def set_time_windows(session: Session, booking: Booking, windows: list[Window]) -> None:
    errors = validate_windows(windows, booking.visits_per_day, booking.visit_duration_minutes)
    if errors:
        raise SchedulingError(" ".join(errors))
    booking.time_windows.clear()
    session.flush()
    for slot, w in enumerate(windows, start=1):
        booking.time_windows.append(BookingTimeWindow(slot=slot, start_time=w.start, end_time=w.end))
    session.flush()


# ============================================================ visit generation
def visit_dates(booking: Booking) -> list[date]:
    return [booking.start_date + timedelta(days=i) for i in range(booking.num_days)]


def expected_visit_count(booking: Booking) -> int:
    return booking.num_days * booking.visits_per_day


def generate_visits(session: Session, booking: Booking) -> list[Visit]:
    """(Re)create one unassigned visit per day per slot inside the resident's windows."""
    windows = booking_windows(booking)
    if len(windows) != booking.visits_per_day:
        raise MissingTimeWindowsError(booking.visits_per_day, len(windows))
    if any(v.assignment_status == VisitStatus.CONFIRMED for v in booking.visits):
        raise SchedulingError("This booking is confirmed; its visits can no longer be regenerated.")
    duration = timedelta(minutes=booking.visit_duration_minutes)
    booking.visits.clear()
    session.flush()
    for day in visit_dates(booking):
        for slot, w in enumerate(windows, start=1):
            booking.visits.append(
                Visit(
                    visit_date=day,
                    slot=slot,
                    window_start=w.start,
                    window_end=w.end,
                    start_time=w.start,
                    end_time=_add(w.start, duration),
                    duration_minutes=booking.visit_duration_minutes,
                    service_type=booking.service_type,
                    assignment_status=VisitStatus.UNASSIGNED,
                )
            )
    session.flush()
    return list(booking.visits)


# ============================================================ schedule context
@dataclass(frozen=True)
class Busy:
    caregiver_id: int
    day: date
    start: time
    end: time
    building: str
    booking_id: int | None
    hard: bool  # True = confirmed visit; False = proposed on a pending booking


@dataclass(frozen=True)
class Issue:
    kind: str     # unassigned | unqualified | time_off | not_working | outside_hours | overlap | soft_overlap
    message: str
    hard: bool = True


@dataclass
class ScheduleContext:
    booking: Booking
    building: str
    species: str
    service: ServiceType
    caregivers: list[Caregiver]
    availability: dict[int, dict[date, list[Block]]]
    time_off: dict[int, list]
    busy: dict[int, list[Busy]]

    def caregiver(self, cid: int) -> Caregiver | None:
        return next((c for c in self.caregivers if c.id == cid), None)


def load_context(session: Session, booking: Booking) -> ScheduleContext:
    caregivers = list(session.scalars(select(Caregiver).order_by(Caregiver.id)))
    ids = [c.id for c in caregivers]
    start, end = booking.start_date, booking.end_date
    busy: dict[int, list[Busy]] = defaultdict(list)
    rows = session.execute(
        select(Visit, Resident.building_name, Booking.status)
        .join(Booking, Visit.booking_id == Booking.id)
        .join(Resident, Booking.resident_id == Resident.id)
        .where(
            Visit.caregiver_id.is_not(None),
            Visit.visit_date.between(start, end),
            Visit.booking_id != booking.id,
            or_(
                Visit.assignment_status == VisitStatus.CONFIRMED,
                (Visit.assignment_status == VisitStatus.PROPOSED) & Booking.status.in_(ACTIVE_PIPELINE),
            ),
        )
    ).all()
    for v, building, _status in rows:
        busy[v.caregiver_id].append(
            Busy(v.caregiver_id, v.visit_date, v.start_time, v.end_time, building, v.booking_id,
                 hard=v.assignment_status == VisitStatus.CONFIRMED)
        )
    return ScheduleContext(
        booking=booking,
        building=booking.resident.building_name,
        species=booking.pet.species.strip().lower(),
        service=booking.service_type,
        caregivers=caregivers,
        availability=availability_map(session, ids, start, end),
        time_off=time_off_map(session, ids, start, end),
        busy=busy,
    )


def travel_buffer(building_a: str, building_b: str) -> timedelta:
    return TRAVEL_BUFFER_SAME_BUILDING if building_a == building_b else TRAVEL_BUFFER_OTHER_BUILDING


def _clashes(day: date, start: time, end: time, building: str, other: Busy) -> bool:
    if other.day != day:
        return False
    buf = travel_buffer(building, other.building)
    return _dt(day, start) < _dt(day, other.end) + buf and _dt(day, other.start) < _dt(day, end) + buf


def qualification_issues(ctx: ScheduleContext, cg: Caregiver) -> list[str]:
    reasons = []
    if not cg.active_status:
        reasons.append("inactive")
    if ctx.species not in cg.species_set:
        reasons.append(f"does not care for {ctx.species}s")
    if ctx.service not in cg.service_set:
        reasons.append(f"not qualified for {SERVICE_LABELS[ctx.service].split(' (')[0].lower()}")
    if ctx.building not in cg.building_set:
        reasons.append(f"does not cover {ctx.building}")
    return reasons


def check_slot(
    ctx: ScheduleContext,
    cid: int,
    day: date,
    start: time,
    end: time,
    planned: Iterable[Busy] = (),
    include_soft: bool = True,
) -> Issue | None:
    """First reason the caregiver cannot do [start, end) on ``day``, or None."""
    off = is_off(ctx.time_off.get(cid, []), day)
    if off:
        return Issue("time_off", f"on time off {day:%b %d}" + (f" ({off.reason})" if off.reason else ""))
    blocks = ctx.availability.get(cid, {}).get(day, [])
    if not blocks:
        return Issue("not_working", f"not working {day:%b %d}")
    if not any(b.start <= start and end <= b.end for b in blocks):
        hours = ", ".join(b.label() for b in blocks)
        return Issue("outside_hours", f"works {hours} on {day:%b %d}")
    for other in ctx.busy.get(cid, []):
        if _clashes(day, start, end, ctx.building, other):
            if other.hard:
                return Issue("overlap", f"already confirmed {day:%b %d} {other.start:%H:%M}–{other.end:%H:%M} (booking #{other.booking_id}, {other.building})")
            if include_soft:
                return Issue("soft_overlap", f"holding {day:%b %d} {other.start:%H:%M}–{other.end:%H:%M} for pending booking #{other.booking_id}", hard=False)
    for other in planned:
        if _clashes(day, start, end, ctx.building, other):
            return Issue("overlap", f"needs travel time after another visit of this booking at {other.start:%H:%M}")
    return None


def _starts_in(window_start: time, window_end: time, minutes: int) -> list[tuple[time, time]]:
    out, cur = [], window_start
    dur = timedelta(minutes=minutes)
    while cur is not None:
        end = _add(cur, dur)
        if end is None or end > window_end:
            break
        out.append((cur, end))
        cur = _add(cur, START_STEP)
    return out


def find_start(ctx: ScheduleContext, cid: int, visit: Visit, planned: list[Busy]) -> tuple[time, time] | Issue:
    first_issue: Issue | None = None
    for s, e in _starts_in(visit.window_start, visit.window_end, visit.duration_minutes):
        issue = check_slot(ctx, cid, visit.visit_date, s, e, planned)
        if issue is None:
            return s, e
        first_issue = first_issue or issue
    return first_issue or Issue("outside_hours", "window too short")


# ================================================================== matching
@dataclass
class Proposal:
    visit_id: int
    day: date
    slot: int
    window: Window
    caregiver_id: int | None
    caregiver_name: str | None
    start: time
    end: time
    blocked_by: dict[str, str] = field(default_factory=dict)  # caregiver name -> reason


@dataclass
class CandidateSummary:
    caregiver_id: int
    name: str
    eligible: bool
    reasons: list[str]
    coverable: int
    same_building_visits: int
    assigned: int = 0
    rank: int | None = None


@dataclass
class Alternative:
    visit_id: int
    caregiver_id: int
    caregiver_name: str
    day: date
    start: time
    end: time
    minutes_outside_window: int


@dataclass
class Recommendation:
    booking_id: int
    building: str
    species: str
    proposals: list[Proposal]
    candidates: list[CandidateSummary]
    alternatives: dict[int, list[Alternative]]

    @property
    def unassigned(self) -> list[Proposal]:
        return [p for p in self.proposals if p.caregiver_id is None]

    @property
    def fully_assigned(self) -> bool:
        return bool(self.proposals) and not self.unassigned

    def facts(self) -> dict:
        """Non-sensitive summary for the AI explanation (no notes, no prices)."""
        per = defaultdict(int)
        for p in self.proposals:
            if p.caregiver_name:
                per[p.caregiver_name.split()[0]] += 1
        ranked = [c for c in self.candidates if c.eligible]
        return {
            "building": self.building,
            "species": self.species,
            "total_visits": len(self.proposals),
            "assigned_visits": len(self.proposals) - len(self.unassigned),
            "visits_per_caregiver": dict(per),
            "top_candidates": [
                {"first_name": c.name.split()[0], "can_cover": c.coverable, "already_in_building": c.same_building_visits}
                for c in ranked[:3]
            ],
            "excluded": {c.name.split()[0]: c.reasons[:2] for c in self.candidates if not c.eligible},
            "unassigned": [
                {"date": p.day.isoformat(), "window": p.window.label(), "why": list(p.blocked_by.values())[:3]}
                for p in self.unassigned
            ],
            "alternatives": [
                {"date": a.day.isoformat(), "time": f"{a.start:%H:%M}–{a.end:%H:%M}", "caregiver": a.caregiver_name.split()[0]}
                for alts in self.alternatives.values() for a in alts
            ],
        }


def _visit_busy(ctx: ScheduleContext, cid: int, day: date, s: time, e: time) -> Busy:
    return Busy(cid, day, s, e, ctx.building, ctx.booking.id, hard=True)


def recommend_schedule(session: Session, booking: Booking) -> Recommendation:
    """Compute (but do not save) the best caregiver for every visit."""
    visits = [v for v in booking.visits if v.assignment_status != VisitStatus.CANCELLED]
    if not visits:
        raise SchedulingError("Generate visits before matching caregivers.")
    ctx = load_context(session, booking)
    days = set(visit_dates(booking))

    summaries: list[CandidateSummary] = []
    for cg in ctx.caregivers:
        reasons = qualification_issues(ctx, cg)
        same_building = sum(1 for b in ctx.busy.get(cg.id, []) if b.building == ctx.building and b.day in days)
        summaries.append(CandidateSummary(cg.id, cg.full_name, not reasons, reasons, 0, same_building))

    eligible = [s for s in summaries if s.eligible]
    for s in eligible:  # how much of this booking could each caregiver do alone?
        planned: list[Busy] = []
        for v in visits:
            res = find_start(ctx, s.caregiver_id, v, planned)
            if not isinstance(res, Issue):
                s.coverable += 1
                planned.append(_visit_busy(ctx, s.caregiver_id, v.visit_date, *res))
        if s.coverable == 0:
            s.reasons.append("no free time in the requested windows")

    load = {cid: len(b) for cid, b in ctx.busy.items()}
    ranked = sorted(eligible, key=lambda s: (-s.coverable, -s.same_building_visits, load.get(s.caregiver_id, 0), s.caregiver_id))
    for i, s in enumerate(ranked, start=1):
        s.rank = i

    planned_by: dict[int, list[Busy]] = defaultdict(list)
    proposals: list[Proposal] = []
    for v in visits:
        p = Proposal(v.id, v.visit_date, v.slot, Window(v.window_start, v.window_end), None, None, v.window_start, v.end_time)
        for s in ranked:
            res = find_start(ctx, s.caregiver_id, v, planned_by[s.caregiver_id])
            if isinstance(res, Issue):
                p.blocked_by[s.name] = res.message
                continue
            p.caregiver_id, p.caregiver_name = s.caregiver_id, s.name
            p.start, p.end = res
            planned_by[s.caregiver_id].append(_visit_busy(ctx, s.caregiver_id, v.visit_date, *res))
            s.assigned += 1
            break
        if p.caregiver_id is None:
            dur = timedelta(minutes=v.duration_minutes)
            p.end = _add(p.start, dur) or p.end
        proposals.append(p)

    alternatives: dict[int, list[Alternative]] = {}
    for p in proposals:
        if p.caregiver_id is None:
            visit = next(v for v in visits if v.id == p.visit_id)
            alternatives[p.visit_id] = _alternatives(ctx, ranked, visit, planned_by)

    order = {s.caregiver_id: (0, s.rank) for s in ranked}
    summaries.sort(key=lambda s: order.get(s.caregiver_id, (1, s.caregiver_id)))
    return Recommendation(booking.id, ctx.building, ctx.species, proposals, summaries, alternatives)


def _alternatives(ctx: ScheduleContext, ranked: list[CandidateSummary], visit: Visit, planned_by) -> list[Alternative]:
    """Nearest feasible times on the same day, for resident approval."""
    best: list[Alternative] = []
    for s in ranked:
        found: Alternative | None = None
        for block in ctx.availability.get(s.caregiver_id, {}).get(visit.visit_date, []):
            for st, en in _starts_in(block.start, block.end, visit.duration_minutes):
                if check_slot(ctx, s.caregiver_id, visit.visit_date, st, en, planned_by[s.caregiver_id]):
                    continue
                if st < visit.window_start:
                    gap = _minutes(st, visit.window_start)
                elif en > visit.window_end:
                    gap = _minutes(visit.window_end, en)
                else:
                    gap = 0
                if found is None or gap < found.minutes_outside_window:
                    found = Alternative(visit.id, s.caregiver_id, s.name, visit.visit_date, st, en, gap)
        if found:
            best.append(found)
    best.sort(key=lambda a: (a.minutes_outside_window, a.start, a.caregiver_id))
    return best[:MAX_ALTERNATIVES]


def apply_recommendation(session: Session, booking: Booking, rec: Recommendation) -> None:
    by_id = {v.id: v for v in booking.visits}
    for p in rec.proposals:
        v = by_id[p.visit_id]
        v.caregiver_id = p.caregiver_id
        v.start_time, v.end_time = p.start, p.end
        v.assignment_status = VisitStatus.PROPOSED if p.caregiver_id else VisitStatus.UNASSIGNED
    session.flush()


# ========================================================= conflict detection
@dataclass(frozen=True)
class Conflict:
    visit_id: int
    day: date
    start: time
    end: time
    caregiver_id: int | None
    kind: str
    message: str
    hard: bool


def check_booking_schedule(session: Session, booking: Booking, include_soft: bool = False) -> list[Conflict]:
    """Re-validate every visit against the database (used before confirmation)."""
    visits = [v for v in booking.visits if v.assignment_status != VisitStatus.CANCELLED]
    if not visits:
        return [Conflict(0, booking.start_date, time(0), time(0), None, "unassigned", "No visits have been generated.", True)]
    ctx = load_context(session, booking)
    out: list[Conflict] = []
    planned: dict[int, list[Busy]] = defaultdict(list)
    for v in sorted(visits, key=lambda v: (v.visit_date, v.start_time)):
        def add(kind: str, msg: str, hard: bool = True) -> None:
            out.append(Conflict(v.id, v.visit_date, v.start_time, v.end_time, v.caregiver_id, kind, msg, hard))

        if v.caregiver_id is None:
            add("unassigned", "No caregiver assigned.")
            continue
        cg = ctx.caregiver(v.caregiver_id)
        reasons = qualification_issues(ctx, cg) if cg else ["unknown caregiver"]
        if reasons:
            add("unqualified", f"{cg.full_name if cg else 'Caregiver'}: {', '.join(reasons)}.")
        issue = check_slot(ctx, v.caregiver_id, v.visit_date, v.start_time, v.end_time, planned[v.caregiver_id], include_soft=include_soft)
        if issue:
            add(issue.kind, f"{cg.full_name if cg else 'Caregiver'} {issue.message}.", issue.hard)
        planned[v.caregiver_id].append(_visit_busy(ctx, v.caregiver_id, v.visit_date, v.start_time, v.end_time))
    return out


def validate_assignment(
    session: Session, booking: Booking, visit: Visit, caregiver_id: int, start: time | None = None
) -> tuple[time, time] | Issue:
    """Can ``caregiver_id`` take ``visit`` (optionally at an explicit start time)?

    Soft holds on other pending bookings are not blocking here (the manager decides);
    confirmed visits, hours, time off and qualifications are.
    """
    ctx = load_context(session, booking)
    cg = ctx.caregiver(caregiver_id)
    if cg is None:
        return Issue("unqualified", "Caregiver not found.")
    reasons = qualification_issues(ctx, cg)
    if reasons:
        return Issue("unqualified", f"{cg.full_name} {', '.join(reasons)}.")
    planned = [
        _visit_busy(ctx, caregiver_id, o.visit_date, o.start_time, o.end_time)
        for o in booking.visits
        if o.id != visit.id and o.caregiver_id == caregiver_id and o.assignment_status != VisitStatus.CANCELLED
    ]
    if start is not None:
        end = _add(start, timedelta(minutes=visit.duration_minutes))
        if end is None:
            return Issue("outside_hours", "Visits cannot run past midnight.")
        issue = check_slot(ctx, caregiver_id, visit.visit_date, start, end, planned, include_soft=False)
        return issue or (start, end)
    first: Issue | None = None
    candidates = [(visit.start_time, visit.end_time)] + _starts_in(visit.window_start, visit.window_end, visit.duration_minutes)
    for s, e in candidates:
        issue = check_slot(ctx, caregiver_id, visit.visit_date, s, e, planned, include_soft=False)
        if issue is None:
            return s, e
        first = first or issue
    return first or Issue("outside_hours", "No time in the window.")


# ================================================================== displays
def visit_rows(visits: Iterable[Visit]) -> list[dict]:
    return [
        {
            "Visit": v.id,
            "Date": v.visit_date,
            "Day": f"{v.visit_date:%a}",
            "Slot": v.slot,
            "Window": f"{v.window_start:%H:%M}–{v.window_end:%H:%M}",
            "Time": f"{v.start_time:%H:%M}–{v.end_time:%H:%M}",
            "Minutes": v.duration_minutes,
            "Service": SERVICE_LABELS[v.service_type].split(" (")[0],
            "Caregiver": v.caregiver.full_name if v.caregiver else "—",
            "Status": v.assignment_status.value.replace("_", " ").title(),
        }
        for v in sorted(visits, key=lambda v: (v.visit_date, v.start_time))
    ]


def windows_from_extracted(items, visits_per_day: int | None, duration_minutes: int = 30) -> tuple[list[Window], list[str]]:
    """Turn AI-extracted times into windows, or explain why we must ask instead.

    A single stated time becomes a 60-minute window starting at that time. If the
    count does not match the visits per day, nothing is guessed: we ask.
    """
    if not items:
        return [], ["What time window would you prefer for each daily visit (e.g. morning 7–10 AM, evening 5–8 PM)?"]
    windows = [Window(*i.as_times(60)) for i in items]
    errors = validate_windows(windows, visits_per_day or 0, duration_minutes)
    if errors:
        return [], [f"Please confirm your visit times: {errors[0]}"]
    return windows, []
