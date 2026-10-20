"""Week 2 synthetic data: capabilities, availability, time off, managers and
bookings at every workflow stage (including deliberate conflicts).

Built on top of the Week 1 seed (same residents, pets and caregivers, looked up by
email). Bookings are created through the real workflow functions, so they obey
every rule. Reproducible and idempotent: rerunning creates nothing new.

    python -m pawpal.seed_data      # Week 1 + Week 2
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time, timedelta

from sqlalchemy import Engine, select

from .actors import Actor
from .availability import Block, request_time_off, set_capabilities, set_day_availability
from .booking_service import create_booking_draft
from .database import init_db, session_scope
from .models import (
    Booking,
    BookingStatus,
    Caregiver,
    CaregiverAvailability,
    CaregiverTimeOff,
    Pet,
    PropertyManager,
    Resident,
    ServiceType as ST,
)
from .schemas import BookingDraft
from .scheduling import Window
from .seed_data import build_dataset
from .workflow import confirm_booking, prepare_booking, resident_approve

SEED_TODAY = date(2026, 10, 15)          # "today" for seeded drafts (keeps reruns valid)
AVAILABILITY_START = date(2026, 10, 19)
AVAILABILITY_END = date(2026, 12, 31)
ALL = list(ST)
BUILDINGS = ["The Meridian", "Hudson Crest", "Aurelia Tower", "Skyline Court", "Harbor House"]

MANAGERS = [
    ("Olivia Grant", "olivia.grant.pm@example.test", None),
    ("Marcus Lee", "marcus.lee.pm@example.test", "The Meridian"),
    ("Priya Shah", "priya.shah.pm@example.test", "Hudson Crest"),
]

AM, PM = Block(time(7), time(12)), Block(time(16), time(21))


@dataclass(frozen=True)
class Profile:
    species: tuple[str, ...]
    services: tuple[ST, ...]
    buildings: tuple[str, ...]
    weekdays: tuple[int, ...]          # Mon = 0
    blocks: tuple[Block, ...]
    time_off: tuple[tuple[date, date, str], ...] = ()


# keyed by caregiver index in the Week 1 dataset (cg0 ... cg9)
PROFILES: dict[int, Profile] = {
    0: Profile(("cat", "dog"), tuple(ALL), ("The Meridian", "Hudson Crest"), (0, 1, 2, 3, 4), (AM, PM)),
    1: Profile(("dog",), (ST.WALKING, ST.FEEDING, ST.CHECK_IN), ("The Meridian", "Aurelia Tower", "Skyline Court"),
               (0, 1, 2, 3, 4, 5), (Block(time(6), time(11)), Block(time(15), time(20)))),
    2: Profile(("cat", "rabbit"), (ST.FEEDING, ST.CHECK_IN, ST.FULL_CARE), ("The Meridian", "Harbor House"),
               tuple(range(7)), (Block(time(7), time(13)), Block(time(17), time(21))),
               ((date(2026, 11, 7), date(2026, 11, 8), "Family wedding"),)),
    3: Profile(("cat", "dog"), tuple(ALL), ("Hudson Crest", "Harbor House", "Skyline Court"),
               (0, 1, 2, 3, 4), (Block(time(8), time(18)),)),
    4: Profile(("cat", "dog"), (ST.WALKING, ST.FEEDING, ST.CHECK_IN), ("Aurelia Tower", "Skyline Court", "The Meridian"),
               (2, 3, 4, 5, 6), (Block(time(7), time(11)), Block(time(16), time(22))),
               ((date(2026, 11, 20), date(2026, 11, 22), "Vacation"),)),
    5: Profile(("cat",), (ST.FEEDING, ST.CHECK_IN), ("Hudson Crest", "The Meridian", "Aurelia Tower"),
               (5, 6, 0), (Block(time(8), time(20)),)),
    6: Profile(("cat", "dog", "rabbit"), tuple(ALL), ("Harbor House", "Aurelia Tower"),
               tuple(range(7)), (Block(time(6, 30), time(10, 30)), Block(time(17), time(20, 30))),
               ((date(2026, 12, 24), date(2026, 12, 26), "Holiday"),)),
    7: Profile(("dog",), (ST.WALKING, ST.FULL_CARE), ("The Meridian", "Hudson Crest"),
               (0, 1, 2, 3, 4), (Block(time(11), time(19)),)),
    8: Profile(("cat",), (ST.FEEDING,), ("The Meridian",), (), ()),   # inactive
    9: Profile(("dog",), (ST.WALKING,), ("Skyline Court",), (), ()),  # inactive
}


@dataclass(frozen=True)
class SeedBooking:
    pet: str
    owner_name: str
    start: date
    end: date
    visits_per_day: int
    service: ST
    windows: tuple[tuple[time, time], ...]
    stage: str          # draft | resident | admin | confirmed | time_off_conflict
    duration: int = 30
    addons: tuple[str, ...] = ()
    photos: bool = True
    note: str | None = None
    conflict: tuple[str, date, str] | None = field(default=None)


W_AM, W_PM = (time(7), time(10)), (time(17), time(20))

BOOKINGS: list[SeedBooking] = [
    # Confirmed first: they hold caregivers' time (hard conflicts for later requests).
    SeedBooking("Leo", "Timothy Watts", date(2026, 11, 5), date(2026, 11, 8), 2, ST.WALKING, (W_AM, W_PM), "confirmed", 60),
    SeedBooking("Pepper", "Sandra Montgomery", date(2026, 11, 6), date(2026, 11, 7), 1, ST.FEEDING, (W_AM,), "confirmed"),
    SeedBooking("Atlas", "Sharon James", date(2026, 11, 10), date(2026, 11, 13), 2, ST.WALKING, (W_AM, (time(16), time(19))), "confirmed"),
    SeedBooking("Bella", "Benjamin Stanley", date(2026, 11, 18), date(2026, 11, 21), 2, ST.FEEDING, (W_AM, W_PM), "confirmed", addons=("litter_box",)),
    SeedBooking("Daisy", "Cristian Santos", date(2026, 11, 6), date(2026, 11, 9), 1, ST.FEEDING, (W_PM,), "confirmed"),
    # Waiting for a property manager.
    SeedBooking("Bruno", "Allen Robinson", date(2026, 11, 13), date(2026, 11, 15), 2, ST.WALKING, (W_AM, W_PM), "admin"),
    SeedBooking("Willow", "Gina Moore", date(2026, 11, 16), date(2026, 11, 18), 1, ST.CHECK_IN, ((time(11), time(14)),), "admin", addons=("plant_watering",)),
    # Pending admin, but the proposed caregiver later took time off -> final check fails.
    SeedBooking("Teddy", "Megan Mcclain", date(2026, 11, 23), date(2026, 11, 24), 1, ST.FEEDING, ((time(8), time(10)),),
                "time_off_conflict", conflict=("time_off", date(2026, 11, 24), "Family emergency")),
    # Waiting for the resident.
    SeedBooking("Juno", "Patty Perez", date(2026, 11, 25), date(2026, 11, 27), 3, ST.WALKING, (W_AM, (time(12), time(14)), W_PM), "resident"),
    SeedBooking("Nala", "Kevin Cox", date(2026, 12, 18), date(2026, 12, 22), 2, ST.FEEDING, (W_AM, (time(18), time(21))), "resident", 60, ("medication",)),
    SeedBooking("Simba", "Brent Abbott", date(2026, 11, 27), date(2026, 11, 29), 1, ST.FULL_CARE, (W_PM,), "resident"),
    # Nobody works that late in Skyline Court -> Needs Rescheduling with alternatives.
    SeedBooking("Louie", "Peter Callahan Jr.", date(2026, 11, 7), date(2026, 11, 8), 1, ST.FEEDING, ((time(22), time(23)),), "resident"),
    # Draft without time windows: the app must ask, not invent times.
    SeedBooking("Milo", "Allen Robinson", date(2026, 12, 3), date(2026, 12, 5), 2, ST.FEEDING, (), "draft"),
]


def _daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def seed_scheduling_data(engine: Engine | None = None) -> dict[str, int]:
    init_db(engine)
    data = build_dataset()
    created = {"managers": 0, "capabilities": 0, "availability_days": 0, "time_off": 0, "bookings": 0}

    with session_scope(engine) as s:
        for name, email, building in MANAGERS:
            if s.scalar(select(PropertyManager).where(PropertyManager.email == email)) is None:
                s.add(PropertyManager(full_name=name, email=email, building_name=building))
                created["managers"] += 1
        s.flush()
        admin = s.scalar(select(PropertyManager).where(PropertyManager.email == MANAGERS[0][1]))
        admin_actor = Actor.manager(admin.id)

        for idx, cg_data in enumerate(data["caregivers"]):
            cg = s.scalar(select(Caregiver).where(Caregiver.email == cg_data["email"]))
            if cg is None:
                continue  # Week 1 seed not run
            prof = PROFILES[idx]
            if not (cg.pet_types or cg.services or cg.buildings):
                set_capabilities(s, cg.id, admin_actor, prof.species, prof.services, prof.buildings)
                created["capabilities"] += 1
            if prof.weekdays:
                have = set(s.scalars(
                    select(CaregiverAvailability.work_date).where(
                        CaregiverAvailability.caregiver_id == cg.id,
                        CaregiverAvailability.work_date.between(AVAILABILITY_START, AVAILABILITY_END),
                    )
                ))
                for d in _daterange(AVAILABILITY_START, AVAILABILITY_END):
                    if d.weekday() in prof.weekdays and d not in have:
                        set_day_availability(s, cg.id, d, prof.blocks, admin_actor)
                        created["availability_days"] += 1
            for start, end, reason in prof.time_off:
                exists = s.scalar(select(CaregiverTimeOff.id).where(
                    CaregiverTimeOff.caregiver_id == cg.id, CaregiverTimeOff.start_date == start,
                    CaregiverTimeOff.end_date == end))
                if not exists:
                    request_time_off(s, cg.id, admin_actor, start, end, reason)
                    created["time_off"] += 1
        admin_id = admin.id

    for sb in BOOKINGS:
        if _seed_booking(engine, sb, admin_id):
            created["bookings"] += 1
    return created


def _seed_booking(engine: Engine | None, sb: SeedBooking, admin_id: int) -> bool:
    with session_scope(engine) as s:
        pet = s.scalar(
            select(Pet).join(Resident, Pet.resident_id == Resident.id)
            .where(Pet.name == sb.pet, Resident.full_name == sb.owner_name)
        )
        if pet is None:
            return False
        exists = s.scalar(select(Booking.id).where(
            Booking.pet_id == pet.id, Booking.start_date == sb.start, Booking.end_date == sb.end))
        if exists:
            return False
        booking = create_booking_draft(
            s, pet.resident_id, pet.id,
            BookingDraft(start_date=sb.start, end_date=sb.end, visits_per_day=sb.visits_per_day,
                         service_type=sb.service, photo_updates_required=sb.photos,
                         special_instructions=sb.note),
            today=SEED_TODAY,
        )
        resident = Actor.resident(pet.resident_id)
        booking_id = booking.id
        if sb.stage == "draft":
            return True
        result = prepare_booking(
            s, booking_id, resident,
            windows=[Window(a, b) for a, b in sb.windows],
            duration_minutes=sb.duration, additional_services=list(sb.addons),
        )
        if sb.stage == "resident" or result.status != BookingStatus.PENDING_RESIDENT_APPROVAL:
            return True
        resident_approve(s, booking_id, resident)
        if sb.conflict:
            _, day, reason = sb.conflict
            cg_id = next(v.caregiver_id for v in s.get(Booking, booking_id).visits if v.visit_date == day)
            request_time_off(s, cg_id, Actor.caregiver(cg_id), day, day, reason)

    if sb.stage == "confirmed":
        confirm_booking(booking_id, Actor.manager(admin_id), engine=engine)
    return True
