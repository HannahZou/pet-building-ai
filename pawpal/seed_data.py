"""Reproducible, idempotent synthetic data generator.

Run:  python -m pawpal.seed_data            (safe to rerun - no duplicates)
      python -m pawpal.seed_data --reset    (drop and recreate all tables first)

Seeds Week 1 data (residents, pets, caregivers, booking history), then the
Week 2 scheduling data (``pawpal.seed_scheduling``) and the Week 3 care-reporting
data (``pawpal.seed_care``).

All people, emails (example.test), phones and pets are fictional.
"""
from __future__ import annotations

import argparse
import random
from datetime import date, datetime, time, timedelta, timezone

from faker import Faker
from sqlalchemy import Engine, select

from .database import get_engine, init_db, session_scope
from .models import (
    Base,
    Booking,
    BookingStatus,
    Caregiver,
    Pet,
    Resident,
    ServiceType,
)

SEED = 42
REFERENCE_DATE = date(2026, 10, 1)  # fixed so history is reproducible
N_RESIDENTS, N_PETS, N_CAREGIVERS, N_BOOKINGS = 20, 30, 10, 50

BUILDINGS = ["The Meridian", "Hudson Crest", "Aurelia Tower", "Skyline Court", "Harbor House"]
PET_NAMES = [
    "Luna", "Milo", "Bella", "Oliver", "Coco", "Max", "Daisy", "Leo", "Willow", "Finn",
    "Nala", "Teddy", "Maple", "Oscar", "Piper", "Ziggy", "Hazel", "Biscuit", "Juno", "Atlas",
    "Pepper", "Rosie", "Simba", "Olive", "Louie", "Ginger", "Mochi", "Cleo", "Bruno", "Poppy",
]
BREEDS = {
    "dog": ["Labrador", "French Bulldog", "Corgi", "Poodle", "Beagle", "Shiba Inu"],
    "cat": ["Domestic Shorthair", "Siamese", "Maine Coon", "Ragdoll", "Bengal"],
    "rabbit": ["Holland Lop", "Mini Rex"],
}
FEEDING = [
    "1 cup dry food, morning and evening", "Half a can of wet food twice daily",
    "Measured kibble, no table scraps", "Grain-free food, fresh water each visit",
]
MEDICAL = [
    None, None, None, "Mild seasonal allergies", "Joint supplement with breakfast",
    "Sensitive stomach, bland diet only", "Daily eye drops",
]
SPECIAL = [
    None, None, "Please close the balcony door", "Shy with strangers, move slowly",
    "Likes a short play session with the feather toy", "Leave the hallway light on",
]


def build_dataset(seed: int = SEED) -> dict[str, list[dict]]:
    """Pure function: same seed -> same data. No database access."""
    Faker.seed(seed)
    fake = Faker("en_US")
    fake.seed_instance(seed)
    rng = random.Random(seed)

    residents = []
    for i in range(N_RESIDENTS):
        name = fake.name()
        slug = "".join(c for c in name.lower() if c.isalpha() or c == " ").replace(" ", ".")
        residents.append(
            {
                "full_name": name,
                "email": f"{slug}.{i}@example.test",
                "building_name": rng.choice(BUILDINGS),
                "apartment_number": f"{rng.randint(2, 45)}{rng.choice('ABCD')}",
                "phone": fake.numerify("555-01##"),
            }
        )

    names = rng.sample(PET_NAMES, N_PETS)
    pets = []
    for i in range(N_PETS):
        owner = i if i < N_RESIDENTS else rng.randrange(N_RESIDENTS)
        species = rng.choices(["dog", "cat", "rabbit"], [5, 5, 1])[0]
        pets.append(
            {
                "owner_email": residents[owner]["email"],
                "name": names[i],
                "species": species,
                "breed": rng.choice(BREEDS[species]),
                "age": rng.randint(1, 15),
                "feeding_instructions": rng.choice(FEEDING),
                "medical_notes": rng.choice(MEDICAL),
            }
        )

    caregivers = []
    for i in range(N_CAREGIVERS):
        name = fake.name()
        slug = "".join(c for c in name.lower() if c.isalpha() or c == " ").replace(" ", ".")
        caregivers.append(
            {
                "full_name": name,
                "email": f"{slug}.cg{i}@example.test",
                "phone": fake.numerify("555-02##"),
                "active_status": i < N_CAREGIVERS - 2,  # last two inactive
            }
        )

    bookings, used = [], set()
    while len(bookings) < N_BOOKINGS:
        pet = rng.choice(pets)
        start = REFERENCE_DATE - timedelta(days=rng.randint(10, 400))
        if (pet["name"], pet["owner_email"], start) in used:
            continue
        used.add((pet["name"], pet["owner_email"], start))
        end = start + timedelta(days=rng.randint(1, 14))
        status = rng.choices(["completed", "cancelled"], [85, 15])[0]
        active = [c for c in caregivers if c["active_status"]]
        bookings.append(
            {
                "owner_email": pet["owner_email"],
                "pet_name": pet["name"],
                "caregiver_email": rng.choice(active)["email"] if status == "completed" else None,
                "start_date": start,
                "end_date": end,
                "visits_per_day": rng.choice([1, 2, 2, 3]),
                "service_type": rng.choice(list(ServiceType)).value,
                "special_instructions": rng.choice(SPECIAL),
                "photo_updates_required": rng.random() < 0.7,
                "status": status,
                "created_at": datetime.combine(
                    start - timedelta(days=rng.randint(3, 30)), time(10, 0), tzinfo=timezone.utc
                ),
            }
        )
    return {"residents": residents, "pets": pets, "caregivers": caregivers, "bookings": bookings}


def seed_database(engine: Engine | None = None, seed: int = SEED) -> dict[str, int]:
    """Insert any missing records. Returns counts of rows newly created."""
    init_db(engine)
    data = build_dataset(seed)
    created = {"residents": 0, "pets": 0, "caregivers": 0, "bookings": 0}

    with session_scope(engine) as s:
        res_by_email: dict[str, Resident] = {}
        for r in data["residents"]:
            obj = s.scalar(select(Resident).where(Resident.email == r["email"]))
            if obj is None:
                obj = Resident(**r)
                s.add(obj)
                created["residents"] += 1
            res_by_email[r["email"]] = obj
        s.flush()

        pet_by_key: dict[tuple[str, str], Pet] = {}
        for p in data["pets"]:
            owner = res_by_email[p["owner_email"]]
            fields = {k: v for k, v in p.items() if k != "owner_email"}
            obj = s.scalar(
                select(Pet).where(Pet.resident_id == owner.id, Pet.name == p["name"])
            )
            if obj is None:
                obj = Pet(resident_id=owner.id, **fields)
                s.add(obj)
                created["pets"] += 1
            pet_by_key[(p["owner_email"], p["name"])] = obj

        cg_by_email: dict[str, Caregiver] = {}
        for c in data["caregivers"]:
            obj = s.scalar(select(Caregiver).where(Caregiver.email == c["email"]))
            if obj is None:
                obj = Caregiver(**c)
                s.add(obj)
                created["caregivers"] += 1
            cg_by_email[c["email"]] = obj
        s.flush()

        for b in data["bookings"]:
            owner = res_by_email[b["owner_email"]]
            pet = pet_by_key[(b["owner_email"], b["pet_name"])]
            exists = s.scalar(
                select(Booking.id).where(
                    Booking.pet_id == pet.id,
                    Booking.start_date == b["start_date"],
                    Booking.end_date == b["end_date"],
                )
            )
            if exists:
                continue
            cg = cg_by_email.get(b["caregiver_email"]) if b["caregiver_email"] else None
            s.add(
                Booking(
                    resident_id=owner.id,
                    pet_id=pet.id,
                    caregiver_id=cg.id if cg else None,
                    start_date=b["start_date"],
                    end_date=b["end_date"],
                    visits_per_day=b["visits_per_day"],
                    service_type=ServiceType(b["service_type"]),
                    special_instructions=b["special_instructions"],
                    photo_updates_required=b["photo_updates_required"],
                    status=BookingStatus(b["status"]),
                    created_at=b["created_at"],
                )
            )
            created["bookings"] += 1
    return created


def seed_all(engine: Engine | None = None) -> dict[str, dict[str, int]]:
    """Week 1 + Week 2 + Week 3 demo data (idempotent)."""
    from .seed_care import seed_care_data
    from .seed_scheduling import seed_scheduling_data

    return {"week1": seed_database(engine), "week2": seed_scheduling_data(engine), "week3": seed_care_data(engine)}


def reset_database(engine: Engine) -> None:
    """Drop every table (including Alembic's version table). Demo use only."""
    from sqlalchemy import text

    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed PawPal AI demo data.")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate all tables first")
    args = parser.parse_args()
    engine = get_engine()
    if args.reset:
        reset_database(engine)
    result = seed_all(engine)
    print("Week 1 created:", result["week1"])
    print("Week 2 created:", result["week2"])
    print("Week 3 created:", result["week3"])


if __name__ == "__main__":
    main()
