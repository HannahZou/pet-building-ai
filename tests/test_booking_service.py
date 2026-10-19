from datetime import date

import pytest

from pawpal.booking_service import (
    BookingValidationError,
    PetOwnershipError,
    create_booking_draft,
    get_booking,
    list_booking_rows,
)
from pawpal.models import BookingStatus, ServiceType
from pawpal.schemas import BookingDraft

TODAY = date(2026, 10, 12)


def draft(**kw):
    base = dict(
        start_date=date(2026, 11, 6), end_date=date(2026, 11, 9), visits_per_day=2,
        service_type=ServiceType.FEEDING, photo_updates_required=True,
        special_instructions="Fresh water please",
    )
    base.update(kw)
    return BookingDraft(**base)


def test_save_and_retrieve_pending_draft(session, two_residents):
    a, _, luna, _ = two_residents
    booking = create_booking_draft(session, a.id, luna.id, draft(), today=TODAY)
    session.commit()
    saved = get_booking(session, booking.id)
    assert saved.status == BookingStatus.PENDING
    assert saved.caregiver_id is None
    assert saved.visits_per_day == 2 and saved.photo_updates_required is True
    rows = list_booking_rows(session, a.id)
    assert rows[0]["pet"] == "Luna" and rows[0]["caregiver"] == "Unassigned"
    assert "medical_notes" not in rows[0] and "SECRET-NOTE" not in str(rows[0])


def test_cannot_book_another_residents_pet(session, two_residents):
    a, _, _, rex = two_residents
    with pytest.raises(PetOwnershipError):
        create_booking_draft(session, a.id, rex.id, draft(), today=TODAY)
    assert list_booking_rows(session, a.id) == []


def test_invalid_range_and_missing_fields_rejected(session, two_residents):
    a, _, luna, _ = two_residents
    with pytest.raises(BookingValidationError):
        create_booking_draft(
            session, a.id, luna.id,
            draft(start_date=date(2026, 11, 9), end_date=date(2026, 11, 6)), today=TODAY,
        )
    with pytest.raises(BookingValidationError):
        create_booking_draft(session, a.id, luna.id, BookingDraft(), today=TODAY)


def test_past_start_date_and_visit_limits_rejected(session, two_residents):
    a, _, luna, _ = two_residents
    for bad in (draft(start_date=date(2026, 10, 1)), draft(visits_per_day=0), draft(visits_per_day=9)):
        with pytest.raises(BookingValidationError):
            create_booking_draft(session, a.id, luna.id, bad, today=TODAY)
