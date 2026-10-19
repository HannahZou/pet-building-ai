import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from pawpal.models import Booking, BookingStatus, ServiceType
from datetime import date


def test_tables_created(engine):
    assert {"residents", "pets", "caregivers", "bookings"} <= set(inspect(engine).get_table_names())


def test_relationships(session, two_residents):
    a, _, luna, _ = two_residents
    assert luna.resident.id == a.id
    assert [p.name for p in a.pets] == ["Luna"]


def _booking(resident_id, pet_id, **kw):
    base = dict(
        resident_id=resident_id, pet_id=pet_id, start_date=date(2026, 11, 6),
        end_date=date(2026, 11, 9), visits_per_day=2, service_type=ServiceType.FEEDING,
        photo_updates_required=True, status=BookingStatus.PENDING,
    )
    base.update(kw)
    return Booking(**base)


def test_booking_relationships_and_nullable_caregiver(session, two_residents):
    a, _, luna, _ = two_residents
    b = _booking(a.id, luna.id)
    session.add(b)
    session.flush()
    assert b.pet.name == "Luna" and b.resident.id == a.id and b.caregiver is None


def test_db_rejects_other_residents_pet(session, two_residents):
    a, _, _, rex = two_residents  # Rex belongs to Ben
    session.add(_booking(a.id, rex.id))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_db_rejects_bad_dates(session, two_residents):
    a, _, luna, _ = two_residents
    session.add(_booking(a.id, luna.id, start_date=date(2026, 11, 9), end_date=date(2026, 11, 6)))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_db_rejects_unknown_resident(session):
    session.add(_booking(999, 999))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
