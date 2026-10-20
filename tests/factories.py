"""Small builders for Week 2 tests. Each helper flushes so ids are available."""
from __future__ import annotations

from datetime import date, time, timedelta

from sqlalchemy.orm import Session

from pawpal.actors import Actor
from pawpal.availability import Block, set_capabilities, set_day_availability
from pawpal.booking_service import create_booking_draft
from pawpal.models import Caregiver, Pet, PropertyManager, Resident, ServiceType
from pawpal.schemas import BookingDraft

TODAY = date(2026, 10, 20)
B1, B2 = "The Meridian", "Hudson Crest"
DAY_HOURS = (Block(time(7), time(12)), Block(time(16), time(21)))
_counter = {"n": 0}


def _n() -> int:
    _counter["n"] += 1
    return _counter["n"]


def resident(s: Session, building: str = B1, name: str = "Ada Resident") -> Resident:
    r = Resident(full_name=name, email=f"res{_n()}@example.test", building_name=building, apartment_number="1A")
    s.add(r)
    s.flush()
    return r


def pet(s: Session, owner: Resident, species: str = "cat", name: str = "Luna") -> Pet:
    p = Pet(resident_id=owner.id, name=name, species=species)
    s.add(p)
    s.flush()
    return p


def manager(s: Session, building: str | None = None, name: str = "Pat Manager") -> PropertyManager:
    m = PropertyManager(full_name=name, email=f"pm{_n()}@example.test", building_name=building)
    s.add(m)
    s.flush()
    return m


def caregiver(
    s: Session,
    name: str = "Casey Care",
    species=("cat", "dog"),
    services=tuple(ServiceType),
    buildings=(B1, B2),
    days: tuple[date, date] | None = (date(2026, 11, 1), date(2026, 11, 30)),
    blocks=DAY_HOURS,
    active: bool = True,
) -> Caregiver:
    cg = Caregiver(full_name=name, email=f"cg{_n()}@example.test", active_status=active)
    s.add(cg)
    s.flush()
    admin = Actor("property_manager", None)  # capability setup bypass (manager role)
    set_capabilities(s, cg.id, admin, species, services, buildings)
    if days:
        d = days[0]
        while d <= days[1]:
            set_day_availability(s, cg.id, d, blocks, admin)
            d += timedelta(days=1)
    return cg


def booking(
    s: Session,
    owner: Resident,
    the_pet: Pet,
    start: date = date(2026, 11, 6),
    end: date = date(2026, 11, 9),
    visits_per_day: int = 2,
    service: ServiceType = ServiceType.FEEDING,
):
    return create_booking_draft(
        s, owner.id, the_pet.id,
        BookingDraft(start_date=start, end_date=end, visits_per_day=visits_per_day,
                     service_type=service, photo_updates_required=True),
        today=TODAY,
    )
