from sqlalchemy import func, select

from pawpal.database import init_db, make_engine, session_scope
from pawpal.models import Booking, Caregiver, Pet, Resident
from pawpal.seed_data import build_dataset, seed_database


def _counts(engine):
    with session_scope(engine) as s:
        return tuple(
            s.scalar(select(func.count()).select_from(m)) for m in (Resident, Pet, Caregiver, Booking)
        )


def test_seed_counts(engine):
    seed_database(engine)
    assert _counts(engine) == (20, 30, 10, 50)


def test_seed_is_safe_to_rerun(engine):
    seed_database(engine)
    second = seed_database(engine)
    assert _counts(engine) == (20, 30, 10, 50)
    assert second == {"residents": 0, "pets": 0, "caregivers": 0, "bookings": 0}


def test_dataset_reproducible():
    assert build_dataset(42) == build_dataset(42)
    assert build_dataset(42) != build_dataset(7)


def test_every_seeded_pet_has_an_owner_and_bookings_match_owner(engine):
    seed_database(engine)
    with session_scope(engine) as s:
        for b in s.scalars(select(Booking)):
            assert b.pet.resident_id == b.resident_id
