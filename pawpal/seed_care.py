"""Week 3 synthetic data: visit executions, care logs, photos, reports, notifications.

Scenarios (fictional people and pets from the Week 1 seed):

* Poppy (cat, The Meridian), Oct 12-13 2026 - every visit completed, photos,
  Oct 12 report approved, Oct 13 report pending review.
* Oscar (dog, The Meridian), Oct 12-13 - walks logged; the Oct 13 evening visit
  is **missed** -> report needs attention, missed-visit notifications.
* Finn (cat, Harbor House), Oct 14 - feeding **not completed** and a **flagged**
  observation -> visit Needs Review, report pending review.
* Coco (dog, Aurelia Tower), Oct 14 - checked in, **no check-out** (in progress).
* Live demo: Ginger (cat, Hudson Crest) has two confirmed visits **today and
  tomorrow** (local date), assigned to Brittany Farmer, so you can try
  Start Visit / Complete Visit in the Caregiver Portal.

Everything goes through the real services (check-in/out use simulated server
times on the visit's own date). Email notifications are left *pending* so the
manager portal can deliver them through the mock provider. Safe to rerun.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from PIL import Image, ImageDraw
from sqlalchemy import select

from .actors import Actor
from .availability import Block, blocks_on, set_day_availability
from .booking_service import create_booking_draft
from .care_logs import log_activity
from .care_reports import approve_report, generate_daily_report
from .database import init_db, session_scope
from .models import (
    ActivityType as A,
    Booking,
    BookingStatus,
    Caregiver,
    CompletionStatus as C,
    Pet,
    PropertyManager,
    Resident,
    ServiceType as ST,
    Visit,
)
from .photos import upload_photo
from .schemas import BookingDraft
from .scheduling import Window
from .seed_scheduling import BUILDINGS, PROFILES
from .seed_data import build_dataset
from .timeutil import building_tz, ensure_building, local_today
from .visit_execution import check_in, check_out, run_daily_checks
from .workflow import confirm_booking, prepare_booking, reassign_visits, resident_approve

SEED_TODAY = date(2026, 10, 1)
HISTORY_DAYS = [date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 14)]
AM, PM = (time(7), time(10)), (time(17), time(20))


@dataclass
class VisitPlan:
    check_in: time | None                       # None = never started (missed)
    check_out: time | None = None               # None with check_in = still in progress
    logs: list[tuple] = field(default_factory=list)   # (activity, status, notes, flagged)
    photo: str | None = None                    # caption


@dataclass
class Scenario:
    pet: str
    owner: str
    start: date
    end: date
    service: ST
    windows: tuple
    days: dict[date, list[VisitPlan]]
    approve: tuple[date, ...] = ()


FED = (A.FEEDING, C.COMPLETED, "Ate all of the food", False)
WATER = (A.FRESH_WATER, C.COMPLETED, None, False)
D12, D13, D14 = HISTORY_DAYS

SCENARIOS = [
    Scenario("Poppy", "Henry Santiago", D12, D13, ST.FEEDING, (AM, PM), {
        D12: [VisitPlan(time(7, 5), time(7, 34), [FED, WATER, (A.LITTER_BOX, C.COMPLETED, None, False)], "Poppy waiting by her bowl"),
              VisitPlan(time(17, 10), time(17, 41), [FED, WATER, (A.PLAYTIME, C.COMPLETED, "Played with the feather wand for 10 minutes", False)],
                        "Evening nap on the window ledge")],
        D13: [VisitPlan(time(7, 2), time(7, 28), [FED, WATER]),
              VisitPlan(time(17, 5), time(17, 33), [FED, WATER, (A.WELLNESS_OBSERVATION, C.COMPLETED, "Calm and relaxed; came to greet me at the door", False)],
                        "Poppy on the sofa")],
    }, approve=(D12,)),
    Scenario("Oscar", "Timothy Watts", D12, D13, ST.WALKING, (AM, PM), {
        D12: [VisitPlan(time(7, 15), time(7, 52), [(A.DOG_WALKING, C.COMPLETED, "30-minute walk around the block", False), WATER], "Oscar after his morning walk"),
              VisitPlan(time(17, 20), time(17, 58), [(A.DOG_WALKING, C.COMPLETED, "Walked to the park and back", False), WATER])],
        D13: [VisitPlan(time(7, 10), time(7, 45), [(A.DOG_WALKING, C.COMPLETED, "25-minute walk", False), WATER]),
              VisitPlan(None)],  # evening visit missed
    }, approve=(D12,)),
    Scenario("Finn", "Austin Gentry", D14, D14, ST.FEEDING, (AM,), {
        D14: [VisitPlan(time(7, 20), time(7, 50), [
            (A.FEEDING, C.NOT_COMPLETED, "Food container was empty; messaged the resident", False), WATER,
            (A.WELLNESS_OBSERVATION, C.COMPLETED, "Finn stayed under the bed for the whole visit and did not come out", True)],
            "Food container (empty)")],
    }),
    Scenario("Coco", "David Hoffman", D14, D14, ST.WALKING, (AM,), {
        D14: [VisitPlan(time(7, 30), None, [(A.DOG_WALKING, C.COMPLETED, "Short 15-minute walk", False)])],  # in progress
    }),
]

LIVE_PET, LIVE_OWNER, LIVE_CAREGIVER = "Ginger", "Monica Herrera", "brittany.farmer.cg5@example.test"
LIVE_WINDOWS = (Window(time(6), time(14)), Window(time(14), time(23)))


def demo_photo(seed: int, label: str) -> bytes:
    """A small, abstract, generated picture (no real photos in the repository)."""
    img = Image.new("RGB", (480, 360), (238, 232, 220))
    draw = ImageDraw.Draw(img)
    hue = (seed * 53) % 255
    for i in range(12):
        r = 30 + i * 12
        cx, cy = 240 + int(80 * math.cos(seed + i)), 180 + int(50 * math.sin(seed + i))
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=((hue + i * 15) % 255, 120, 200 - i * 10), width=3)
    draw.rectangle([0, 320, 480, 360], fill=(31, 42, 68))
    draw.text((14, 332), f"PawPal demo photo - {label}", fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _local(session, booking_id: int, day: date, t: time) -> datetime:
    booking = session.get(Booking, booking_id)
    return datetime.combine(day, t, tzinfo=building_tz(session, booking.resident.building_name))


def _ensure_availability(session, admin: Actor, days: list[date], whole_day_for: str | None = None) -> None:
    data = build_dataset()
    for idx, cg_data in enumerate(data["caregivers"]):
        cg = session.scalar(select(Caregiver).where(Caregiver.email == cg_data["email"]))
        if cg is None or not cg.active_status:
            continue
        for d in days:
            if whole_day_for == cg_data["email"]:
                if not any(b.start <= time(6) and b.end >= time(23) for b in blocks_on(session, cg.id, d)):
                    try:
                        set_day_availability(session, cg.id, d, [Block(time(6), time(23))], admin)
                    except Exception:
                        pass
            elif not blocks_on(session, cg.id, d) and d.weekday() in PROFILES[idx].weekdays:
                set_day_availability(session, cg.id, d, list(PROFILES[idx].blocks), admin)


def _create_confirmed(engine, pet_name, owner, start, end, service, windows, admin_id, assign_to=None) -> int | None:
    with session_scope(engine) as s:
        pet = s.scalar(select(Pet).join(Resident, Pet.resident_id == Resident.id)
                       .where(Pet.name == pet_name, Resident.full_name == owner))
        if pet is None:
            return None
        if s.scalar(select(Booking.id).where(Booking.pet_id == pet.id, Booking.start_date == start, Booking.end_date == end)):
            return None
        booking = create_booking_draft(s, pet.resident_id, pet.id, BookingDraft(
            start_date=start, end_date=end, visits_per_day=len(windows), service_type=service,
            photo_updates_required=True), today=min(SEED_TODAY, start))
        bid, resident = booking.id, Actor.resident(pet.resident_id)
        prepare_booking(s, bid, resident, windows=[w if isinstance(w, Window) else Window(*w) for w in windows])
        if assign_to:
            cg = s.scalar(select(Caregiver).where(Caregiver.email == assign_to))
            b = s.get(Booking, bid)
            if cg and any(v.caregiver_id != cg.id for v in b.visits):
                reassign_visits(s, bid, [v.id for v in b.visits], cg.id, Actor.manager(admin_id))
        if s.get(Booking, bid).status != BookingStatus.PENDING_RESIDENT_APPROVAL:
            return None
        resident_approve(s, bid, resident)
    result = confirm_booking(bid, Actor.manager(admin_id), engine=engine)
    return bid if result.ok else None


def _play_visit(engine, booking_id: int, visit_id: int, day: date, plan: VisitPlan, seed: int) -> None:
    if plan.check_in is None:
        return
    with session_scope(engine) as s:
        cg = Actor.caregiver(s.get(Visit, visit_id).caregiver_id)
        start = _local(s, booking_id, day, plan.check_in)
        check_in(s, visit_id, cg, now=start)
        for i, (activity, status, notes, flagged) in enumerate(plan.logs):
            row = log_activity(s, visit_id, cg, activity, status, notes, flagged)
            row.recorded_at = start + timedelta(minutes=3 + 4 * i)
        if plan.photo:
            photo = upload_photo(s, visit_id, cg, f"seed-{visit_id}.jpg", demo_photo(seed, plan.photo), plan.photo)
            photo.uploaded_at = start + timedelta(minutes=12)
        if plan.check_out:
            check_out(s, visit_id, cg, now=_local(s, booking_id, day, plan.check_out))


def seed_care_data(engine=None) -> dict[str, int]:
    init_db(engine)
    created = {"bookings": 0, "visits_played": 0, "reports": 0, "approved": 0, "live_demo": 0}
    with session_scope(engine) as s:
        for b in BUILDINGS:
            ensure_building(s, b)
        admin = s.scalar(select(PropertyManager).where(PropertyManager.email == "olivia.grant.pm@example.test"))
        if admin is None:
            return created  # Week 2 seed not run
        admin_id = admin.id
        _ensure_availability(s, Actor.manager(admin_id), HISTORY_DAYS)

    for n, sc in enumerate(SCENARIOS):
        bid = _create_confirmed(engine, sc.pet, sc.owner, sc.start, sc.end, sc.service, sc.windows, admin_id)
        if bid is None:
            continue
        created["bookings"] += 1
        for day, plans in sc.days.items():
            with session_scope(engine) as s:
                visit_ids = [v.id for v in s.get(Booking, bid).visits if v.visit_date == day]
            for i, (vid, plan) in enumerate(zip(visit_ids, plans)):
                _play_visit(engine, bid, vid, day, plan, seed=n * 10 + i)
                created["visits_played"] += 1
        if any(p.check_in is None for plans in sc.days.values() for p in plans):
            with session_scope(engine) as s:  # the day after: the unstarted visit becomes Missed
                last = max(sc.days)
                run_daily_checks(s, now=_local(s, bid, last + timedelta(days=1), time(8)))
        for day in sc.days:
            with session_scope(engine) as s:
                report = generate_daily_report(s, bid, day, use_ai=False)
                created["reports"] += 1
                if day in sc.approve:
                    approve_report(s, report.id, Actor.manager(admin_id), "Checked against visit records.")
                    created["approved"] += 1

    with session_scope(engine) as s:
        today = local_today(building_tz(s, "Hudson Crest"))
        _ensure_availability(s, Actor.manager(admin_id), [today, today + timedelta(days=1)], whole_day_for=LIVE_CAREGIVER)
    if _create_confirmed(engine, LIVE_PET, LIVE_OWNER, today, today + timedelta(days=1), ST.FEEDING,
                         LIVE_WINDOWS, admin_id, assign_to=LIVE_CAREGIVER):
        created["live_demo"] = 1
    return created
