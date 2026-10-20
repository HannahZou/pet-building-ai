"""Booking business logic: validation, ownership checks, persistence.

Every saved booking starts as a ``draft`` (Week 1 called it "pending") with no
caregiver; Week 2's workflow (``pawpal.workflow``) takes it from there. Nothing
here logs special instructions or medical notes.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    MAX_VISITS_PER_DAY,
    Booking,
    BookingStatus,
    Pet,
    Resident,
)
from .schemas import BookingDraft, ExtractedBooking, ValidationResult

logger = logging.getLogger(__name__)

LONG_STAY_DAYS = 30

FIELD_QUESTIONS = {
    "start_date": "What date should care start?",
    "end_date": "What date should care end?",
    "visits_per_day": "How many visits per day would you like?",
    "service_type": "Which service do you need: feeding, walking, check-in, or full care?",
    "photo_updates_required": "Would you like photo updates after visits (yes or no)?",
}


class BookingError(Exception):
    """Base class for booking service errors."""


class BookingValidationError(BookingError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class PetOwnershipError(BookingError):
    pass


class NotFoundError(BookingError):
    pass


# ---------------------------------------------------------------- validation
def validate_draft(draft: BookingDraft, today: date | None = None) -> ValidationResult:
    today = today or date.today()
    result = ValidationResult()

    for field, question in FIELD_QUESTIONS.items():
        if getattr(draft, field) is None:
            result.missing_fields.append(field)
            result.errors.append(f"Missing: {question}")

    if draft.start_date and draft.end_date:
        if draft.end_date < draft.start_date:
            result.errors.append("The end date cannot be earlier than the start date.")
        elif (draft.end_date - draft.start_date).days + 1 > LONG_STAY_DAYS:
            result.warnings.append(
                f"This stay is longer than {LONG_STAY_DAYS} days. Please double-check the dates."
            )

    if draft.start_date and draft.start_date < today:
        result.errors.append("The start date is in the past.")

    if draft.visits_per_day is not None and not (
        1 <= draft.visits_per_day <= MAX_VISITS_PER_DAY
    ):
        result.errors.append(
            f"Visits per day must be between 1 and {MAX_VISITS_PER_DAY}."
        )
    return result


def pet_name_mismatch(extracted_name: str | None, selected_pet_name: str) -> str | None:
    if extracted_name and extracted_name.strip().lower() != selected_pet_name.strip().lower():
        return (
            f"Your request mentions '{extracted_name}' but the selected pet is "
            f"'{selected_pet_name}'. Select the right pet and generate again."
        )
    return None


def draft_from_extraction(extracted: ExtractedBooking) -> BookingDraft:
    return BookingDraft(
        start_date=extracted.start_date,
        end_date=extracted.end_date,
        visits_per_day=extracted.visits_per_day,
        service_type=extracted.service_type,
        special_instructions=extracted.special_instructions,
        photo_updates_required=extracted.photo_updates_required,
    )


def review_extraction(
    extracted: ExtractedBooking, selected_pet_name: str, today: date | None = None
) -> ValidationResult:
    """Validate an AI extraction independently of anything the model claims."""
    result = validate_draft(draft_from_extraction(extracted), today)
    mismatch = pet_name_mismatch(extracted.pet_name, selected_pet_name)
    if mismatch:
        result.errors.insert(0, mismatch)
    return result


# --------------------------------------------------------------- persistence
def create_booking_draft(
    session: Session,
    resident_id: int,
    pet_id: int,
    draft: BookingDraft,
    today: date | None = None,
) -> Booking:
    """Save a pending booking draft. Flushes; the caller commits."""
    result = validate_draft(draft, today)
    if not result.ok:
        raise BookingValidationError(result.errors)

    if session.get(Resident, resident_id) is None:
        raise NotFoundError("Resident not found.")
    pet = session.get(Pet, pet_id)
    if pet is None:
        raise NotFoundError("Pet not found.")
    if pet.resident_id != resident_id:
        raise PetOwnershipError("Residents can only book care for their own pets.")

    booking = Booking(
        resident_id=resident_id,
        pet_id=pet_id,
        caregiver_id=None,  # never assigned in Week 1
        start_date=draft.start_date,
        end_date=draft.end_date,
        visits_per_day=draft.visits_per_day,
        service_type=draft.service_type,
        special_instructions=draft.special_instructions,
        photo_updates_required=bool(draft.photo_updates_required),
        status=BookingStatus.DRAFT,
    )
    session.add(booking)
    session.flush()
    logger.info("Saved pending booking draft id=%s", booking.id)  # ids only
    return booking


def get_booking(session: Session, booking_id: int) -> Booking | None:
    return session.get(Booking, booking_id)


# ------------------------------------------------------------------- queries
def list_residents(session: Session) -> list[Resident]:
    return list(session.scalars(select(Resident).order_by(Resident.full_name)))


def list_pets_for_resident(session: Session, resident_id: int) -> list[Pet]:
    return list(
        session.scalars(
            select(Pet).where(Pet.resident_id == resident_id).order_by(Pet.name)
        )
    )


def _caregiver_label(b: Booking) -> str:
    """Week 1 stored one caregiver per booking; Week 2 assigns caregivers per visit."""
    if b.caregiver:
        return b.caregiver.full_name
    names = sorted({v.caregiver.full_name for v in b.visits
                    if v.caregiver and v.assignment_status.value != "cancelled"})
    return ", ".join(names) if names else "Unassigned"


def list_booking_rows(session: Session, resident_id: int) -> list[dict[str, Any]]:
    """Plain dicts for display. Excludes medical notes."""
    bookings = session.scalars(
        select(Booking)
        .where(Booking.resident_id == resident_id)
        .order_by(Booking.created_at.desc(), Booking.id.desc())
    ).all()
    return [
        {
            "id": b.id,
            "pet": b.pet.name,
            "start_date": b.start_date,
            "end_date": b.end_date,
            "visits_per_day": b.visits_per_day,
            "service_type": b.service_type.value,
            "photo_updates": b.photo_updates_required,
            "status": b.status.value,
            "caregiver": _caregiver_label(b),
            "special_instructions": b.special_instructions or "",
            "created_at": b.created_at,
        }
        for b in bookings
    ]
