"""Week 4 synthetic history for analytics (clearly labelled synthetic).

Twelve weeks (from 2026-07-06) of bookings across all five buildings, created
through the real Week 2-3 services so every rule applies, then given realistic
timestamps (request -> quote -> approvals -> visits -> report -> notification):

* demand grows week over week, with more stays starting Thursday-Saturday
* ~10% of requests ask for a late-night window -> scheduling conflict; half accept
  an alternative time, half are rejected by the resident
* some quotes are rejected, some requests are abandoned as drafts
* visits: mostly completed, some late check-ins, some missed, some with an
  essential task not completed
* daily reports (template text) approved by managers; some are rejected first and
  regenerated (human correction)
* email notification outcomes: mostly sent, a few failed

All residents involved are the fictional ``@example.test`` residents, flagged
``is_synthetic``. Fixed random seed -> reproducible; already-seeded bookings are
skipped, so it is safe to rerun.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from .actors import Actor
from .availability import blocks_on, set_day_availability
from .booking_service import create_booking_draft
from .care_logs import essential_activities, log_activity
from .care_reports import approve_report, generate_daily_report, reject_report
from .database import session_scope
from .models import (
    AuditEvent, Booking, BookingEvent, BookingQuote, BookingStatus as S, Caregiver, CompletionStatus as C,
    DailyCareReport, DeliveryStatus, Notification, NotificationChannel, Pet, PropertyManager, Resident, ServiceType as ST,
    Visit,
)
from .schemas import BookingDraft
from .scheduling import Window
from .seed_data import build_dataset
from .seed_scheduling import PROFILES
from .timeutil import building_tz
from .visit_execution import check_in, check_out, run_daily_checks
from .workflow import (
    accept_alternative, cancel_booking, confirm_booking, mark_completed, prepare_booking, resident_approve, resident_reject,
)

HISTORY_START = date(2026, 7, 6)   # a Monday
SEED = 2026
WINDOWS = {
    1: [(Window(time(7), time(10)),), (Window(time(17), time(20)),), (Window(time(11), time(14)),)],
    2: [(Window(time(7), time(10)), Window(time(17), time(20)))],
}
LATE_WINDOW = (Window(time(21, 30), time(23)),)
UTC = timezone.utc


@dataclass
class Plan:
    pet_id: int
    resident_id: int
    building: str
    species: str
    start: date
    nights: int
    vpd: int
    service: ST
    windows: tuple
    created: datetime
    outcome: str      # confirmed | conflict_accept | conflict_reject | quote_rejected | abandoned


def _pets(session) -> list[Pet]:
    return [p for p in session.scalars(select(Pet).join(Resident, Pet.resident_id == Resident.id)
                                       .where(Resident.is_synthetic.is_(True)).order_by(Pet.id))
            if p.species in ("cat", "dog")]


def build_plans(session, weeks: int, rng: random.Random) -> list[Plan]:
    pets = _pets(session)
    plans = []
    for w in range(weeks):
        n = 4 + round(w * 0.55) + rng.randint(0, 1)            # growing demand
        week_start = HISTORY_START + timedelta(weeks=w)
        for _ in range(n):
            day = rng.choices(range(7), weights=[1, 1, 1, 2, 3, 3, 1])[0]   # Thu-Sat heavy
            pet = rng.choice(pets)
            service = rng.choice([ST.FEEDING, ST.FEEDING, ST.CHECK_IN]) if pet.species == "cat" \
                else rng.choice([ST.WALKING, ST.WALKING, ST.FEEDING])
            vpd = rng.choice([1, 2, 2])
            roll = rng.random()
            outcome = ("abandoned" if roll < 0.05 else "quote_rejected" if roll < 0.12 else
                       "conflict_accept" if roll < 0.17 else "conflict_reject" if roll < 0.22 else "confirmed")
            start = week_start + timedelta(days=day)
            lead = rng.randint(2, 14)
            created = datetime.combine(start - timedelta(days=lead), time(rng.randint(7, 21), rng.randint(0, 59)), tzinfo=UTC)
            windows = LATE_WINDOW if outcome.startswith("conflict") else rng.choice(WINDOWS[vpd])
            plans.append(Plan(pet.id, pet.resident_id, pet.resident.building_name, pet.species, start,
                              rng.randint(0, 3), len(windows), service, windows, created, outcome))
    return plans


def _ensure_history_availability(session, days: list[date]) -> None:
    data = build_dataset()
    admin = Actor("property_manager", None)
    for idx, cg_data in enumerate(data["caregivers"]):
        cg = session.scalar(select(Caregiver).where(Caregiver.email == cg_data["email"]))
        if cg is None or not cg.active_status:
            continue
        for d in days:
            if d.weekday() in PROFILES[idx].weekdays and not blocks_on(session, cg.id, d):
                set_day_availability(session, cg.id, d, list(PROFILES[idx].blocks), admin)


def _manager_for(session, building: str) -> int:
    local = session.scalar(select(PropertyManager.id).where(PropertyManager.building_name == building))
    return local or session.scalar(select(PropertyManager.id).where(PropertyManager.building_name.is_(None)))


def _restamp(session, booking_id: int, created: datetime, rng: random.Random) -> datetime:
    """Give a booking's request/workflow events realistic times. Returns the confirmation time."""
    b = session.get(Booking, booking_id)
    b.created_at = created
    t = created
    for ev in session.scalars(select(BookingEvent).where(BookingEvent.booking_id == booking_id).order_by(BookingEvent.id)):
        if ev.to_status == S.QUOTED.value:
            t += timedelta(minutes=rng.randint(2, 50))
        elif ev.from_status == S.QUOTED.value or ev.actor_role == "system":
            t += timedelta(seconds=rng.randint(5, 90))
        elif ev.to_status == S.PENDING_ADMIN_APPROVAL.value:
            t += timedelta(hours=rng.uniform(1.5, 30))
        elif ev.to_status in (S.CONFIRMED.value, S.NEEDS_RESCHEDULING.value):
            t += timedelta(hours=rng.uniform(0.5, 26))
        else:
            t += timedelta(hours=rng.uniform(0.2, 6))
        ev.created_at = t
    for q in session.scalars(select(BookingQuote).where(BookingQuote.booking_id == booking_id)):
        q.created_at = created + timedelta(minutes=5)
    for n in session.scalars(select(Notification).where(Notification.booking_id == booking_id)):
        n.created_at = t
    return t


def _play(engine, booking_id: int, rng: random.Random, stats: dict) -> None:
    with session_scope(engine) as s:
        b = s.get(Booking, booking_id)
        tz = building_tz(s, b.resident.building_name)
        species = b.pet.species
        visits = [(v.id, v.visit_date, v.start_time, v.caregiver_id) for v in b.visits]
    for vid, day, start, cg_id in visits:
        roll = rng.random()
        if roll < 0.04:
            stats["missed"] += 1
            continue
        late = roll > 0.90
        offset = rng.randint(35, 75) if late else rng.randint(-8, 12)
        actor = Actor.caregiver(cg_id)
        arrive = datetime.combine(day, start, tzinfo=tz) + timedelta(minutes=offset)
        if arrive.date() != day:
            arrive = datetime.combine(day, start, tzinfo=tz)
        with session_scope(engine) as s:
            check_in(s, vid, actor, now=arrive)
            visit = s.get(Visit, vid)
            for i, activity in enumerate(essential_activities(visit.service_type, species)):
                if i == 0 and roll < 0.09:
                    log_activity(s, vid, actor, activity, C.NOT_COMPLETED, "Could not complete; resident informed")
                else:
                    note = "Calm and relaxed; normal behaviour" if activity.value == "wellness_observation" else None
                    log_activity(s, vid, actor, activity, C.COMPLETED, note)
            check_out(s, vid, actor, now=arrive + timedelta(minutes=rng.randint(22, 42)))
        stats["late"] += late


ADMINS = [
    ("Avery Chen", "avery.chen.admin@example.test", ["analytics:read", "evaluation:read", "evaluation:run", "reports:export"]),
    ("Sam Rivera", "sam.rivera.admin@example.test", ["analytics:read"]),  # dashboards only: no evaluation, no export
]


def ensure_admins(engine=None) -> int:
    from .models import PlatformAdmin

    created = 0
    with session_scope(engine) as s:
        for name, email, perms in ADMINS:
            if s.scalar(select(PlatformAdmin.id).where(PlatformAdmin.email == email)) is None:
                s.add(PlatformAdmin(full_name=name, email=email, permissions=perms))
                created += 1
    return created


def seed_analytics_history(engine=None, weeks: int = 12) -> dict[str, int]:
    rng = random.Random(SEED)
    ensure_admins(engine)
    stats = {"bookings": 0, "confirmed": 0, "conflicts": 0, "rejected": 0, "abandoned": 0,
             "missed": 0, "late": 0, "reports": 0, "corrected": 0, "emails_failed": 0}
    with session_scope(engine) as s:
        if s.scalar(select(PropertyManager.id).limit(1)) is None:
            return stats  # Week 2 seed has not run
        plans = build_plans(s, weeks, rng)
        last = max(p.start + timedelta(days=p.nights) for p in plans)
        _ensure_history_availability(s, [HISTORY_START + timedelta(days=i) for i in range((last - HISTORY_START).days + 1)])

    for p in plans:
        end = p.start + timedelta(days=p.nights)
        with session_scope(engine) as s:
            if s.scalar(select(Booking.id).where(Booking.pet_id == p.pet_id, Booking.start_date == p.start,
                                                 Booking.end_date == end)):
                continue
            mgr = Actor.manager(_manager_for(s, p.building))
            resident = Actor.resident(p.resident_id)
            booking = create_booking_draft(s, p.resident_id, p.pet_id, BookingDraft(
                start_date=p.start, end_date=end, visits_per_day=p.vpd, service_type=p.service,
                photo_updates_required=rng.random() < 0.7), today=p.start - timedelta(days=1))
            bid = booking.id
            stats["bookings"] += 1
            if p.outcome == "abandoned":
                s.get(Booking, bid).created_at = p.created
                stats["abandoned"] += 1
                continue
            result = prepare_booking(s, bid, resident, windows=list(p.windows))
            if result.status == S.NEEDS_RESCHEDULING:
                stats["conflicts"] += 1
                if p.outcome == "conflict_accept":
                    for vid, alts in result.recommendation.alternatives.items():
                        if alts:
                            accept_alternative(s, bid, vid, alts[0].caregiver_id, alts[0].start, resident)
                if s.get(Booking, bid).status == S.NEEDS_RESCHEDULING:
                    resident_reject(s, bid, resident, "Times did not work for me")
            elif p.outcome in ("conflict_reject", "quote_rejected"):
                resident_reject(s, bid, resident, "Decided not to book")
            if s.get(Booking, bid).status == S.REJECTED:
                stats["rejected"] += 1
                _restamp(s, bid, p.created, rng)
                continue
            resident_approve(s, bid, resident)
        if not confirm_booking(bid, mgr, engine=engine).ok:
            with session_scope(engine) as s:
                _restamp(s, bid, p.created, rng)
            continue
        stats["confirmed"] += 1
        with session_scope(engine) as s:
            _restamp(s, bid, p.created, rng)
        _play(engine, bid, rng, stats)
        with session_scope(engine) as s:
            run_daily_checks(s, now=datetime.combine(end + timedelta(days=1), time(14), tzinfo=UTC))
        with session_scope(engine) as s:
            days = sorted({v.visit_date for v in s.get(Booking, bid).visits})
        for day in days:
            with session_scope(engine) as s:
                report = generate_daily_report(s, bid, day, use_ai=False)  # demo data never calls OpenAI
                rid = report.id
                last_out = max([e for e in [_last_out(s, bid, day)] if e] or [datetime.combine(day, time(21), tzinfo=UTC)])
                if rng.random() < 0.1:
                    reject_report(s, rid, mgr, "Please re-check the visit notes before publishing")
                    generate_daily_report(s, bid, day, actor=mgr, use_ai=False)
                    stats["corrected"] += 1
                approve_report(s, rid, mgr)
                r = s.get(DailyCareReport, rid)
                r.generated_at = last_out + timedelta(minutes=rng.randint(20, 120))
                r.reviewed_at = r.generated_at + timedelta(hours=rng.uniform(1, 20))
                stats["reports"] += 1
        with session_scope(engine) as s:
            mark_completed(s, bid, mgr, today=end + timedelta(days=2))
            for n in s.scalars(select(Notification).where(Notification.booking_id == bid)):
                if n.created_at.year >= 2026 and n.created_at.date() > end + timedelta(days=3):
                    n.created_at = datetime.combine(end, time(20), tzinfo=UTC)
                if n.channel == NotificationChannel.EMAIL and n.delivery_status == DeliveryStatus.PENDING:
                    n.attempts = 1
                    if rng.random() < 0.04:
                        n.delivery_status, n.last_error = DeliveryStatus.FAILED, "ConnectionError"
                        stats["emails_failed"] += 1
                    else:
                        n.delivery_status, n.provider, n.sent_at = DeliveryStatus.SENT, "mock", n.created_at
    return stats


def _last_out(session, booking_id: int, day: date):
    from .analytics.data import last_checkout

    return last_checkout(session, booking_id, day)
