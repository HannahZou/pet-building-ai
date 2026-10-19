"""Pydantic schemas.

``ExtractedBooking``  - what the LLM may return (everything optional: the model
                        must leave a field null rather than guess).
``BookingDraft``      - the editable, resident-reviewed draft that gets validated
                        and saved.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from .models import ServiceType

SERVICE_LABELS = {
    ServiceType.FEEDING: "Feeding",
    ServiceType.WALKING: "Walking",
    ServiceType.CHECK_IN: "Check-in",
    ServiceType.FULL_CARE: "Full care (feeding + walking + check-in)",
}


def _blank_to_none(value):
    if isinstance(value, str) and not value.strip():
        return None
    return value.strip() if isinstance(value, str) else value


class ExtractedBooking(BaseModel):
    pet_name: Optional[str] = Field(
        None, description="Pet's name exactly as stated. Null if no name is given."
    )
    start_date: Optional[date] = Field(
        None, description="First day of care (ISO date). Null unless a full date incl. year is stated or unambiguous."
    )
    end_date: Optional[date] = Field(
        None, description="Last day of care (ISO date). Null unless explicitly stated."
    )
    visits_per_day: Optional[int] = Field(
        None, description="Visits per day. Null unless stated (e.g. 'twice a day' = 2)."
    )
    service_type: Optional[ServiceType] = Field(
        None, description="feeding, walking, check_in or full_care. Null if not clear."
    )
    special_instructions: Optional[str] = Field(
        None,
        description="Care instructions the resident explicitly stated. Never add medical or care details they did not state.",
    )
    photo_updates_required: Optional[bool] = Field(
        None, description="True/false only if the resident said so. Null otherwise."
    )
    clarification_questions: list[str] = Field(
        default_factory=list,
        description="Short questions for each missing or ambiguous required detail.",
    )

    @field_validator("pet_name", "special_instructions", mode="before")
    @classmethod
    def clean_text(cls, value):
        return _blank_to_none(value)


class BookingDraft(BaseModel):
    """Everything optional so partially-filled drafts can be validated and shown."""

    start_date: Optional[date] = None
    end_date: Optional[date] = None
    visits_per_day: Optional[int] = None
    service_type: Optional[ServiceType] = None
    special_instructions: Optional[str] = None
    photo_updates_required: Optional[bool] = None

    @field_validator("special_instructions", mode="before")
    @classmethod
    def clean_text(cls, value):
        return _blank_to_none(value)


class ValidationResult(BaseModel):
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors
