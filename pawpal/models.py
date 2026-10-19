"""SQLAlchemy ORM models.

Ownership rule (enforced in the database, not just in Python):
``bookings (pet_id, resident_id)`` is a composite foreign key onto
``pets (id, resident_id)``. A booking therefore cannot reference a pet that
belongs to a different resident, even if someone bypasses the service layer.
"""
from __future__ import annotations

import enum
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MAX_VISITS_PER_DAY = 6


class Base(DeclarativeBase):
    pass


class ServiceType(str, enum.Enum):
    FEEDING = "feeding"
    WALKING = "walking"
    CHECK_IN = "check_in"
    FULL_CARE = "full_care"


class BookingStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"  # Not used by the app in Week 1
    COMPLETED = "completed"
    CANCELLED = "cancelled"


def _enum_col(enum_cls: type[enum.Enum], name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        length=32,
        create_constraint=True,
        values_callable=lambda e: [m.value for m in e],
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Resident(Base):
    __tablename__ = "residents"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    building_name: Mapped[str] = mapped_column(String(120), nullable=False)
    apartment_number: Mapped[str] = mapped_column(String(20), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(40))

    pets: Mapped[list["Pet"]] = relationship(back_populates="resident")
    bookings: Mapped[list["Booking"]] = relationship(
        back_populates="resident",
        foreign_keys="Booking.resident_id",
        primaryjoin="Resident.id == Booking.resident_id",
    )


class Pet(Base):
    __tablename__ = "pets"
    __table_args__ = (
        # Target of the composite FK from bookings (SQLite/Postgres need a unique key).
        UniqueConstraint("id", "resident_id", name="uq_pet_id_resident_id"),
        CheckConstraint("age IS NULL OR age >= 0", name="ck_pet_age_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    resident_id: Mapped[int] = mapped_column(
        ForeignKey("residents.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    species: Mapped[str] = mapped_column(String(40), nullable=False)
    breed: Mapped[str | None] = mapped_column(String(80))
    age: Mapped[int | None] = mapped_column(Integer)
    feeding_instructions: Mapped[str | None] = mapped_column(Text)
    medical_notes: Mapped[str | None] = mapped_column(Text)  # sensitive: never log

    resident: Mapped["Resident"] = relationship(back_populates="pets")
    bookings: Mapped[list["Booking"]] = relationship(
        back_populates="pet",
        foreign_keys="Booking.pet_id",
        primaryjoin="Pet.id == Booking.pet_id",
    )


class Caregiver(Base):
    __tablename__ = "caregivers"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    phone: Mapped[str | None] = mapped_column(String(40))
    active_status: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    bookings: Mapped[list["Booking"]] = relationship(
        back_populates="caregiver",
        foreign_keys="Booking.caregiver_id",
        primaryjoin="Caregiver.id == Booking.caregiver_id",
    )


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["pet_id", "resident_id"],
            ["pets.id", "pets.resident_id"],
            name="fk_booking_pet_belongs_to_resident",
        ),
        CheckConstraint("end_date >= start_date", name="ck_booking_date_order"),
        CheckConstraint(
            f"visits_per_day BETWEEN 1 AND {MAX_VISITS_PER_DAY}",
            name="ck_booking_visits_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    resident_id: Mapped[int] = mapped_column(
        ForeignKey("residents.id"), nullable=False, index=True
    )
    pet_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    caregiver_id: Mapped[int | None] = mapped_column(
        ForeignKey("caregivers.id"), nullable=True
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    visits_per_day: Mapped[int] = mapped_column(Integer, nullable=False)
    service_type: Mapped[ServiceType] = mapped_column(
        _enum_col(ServiceType, "service_type"), nullable=False
    )
    special_instructions: Mapped[str | None] = mapped_column(Text)
    photo_updates_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[BookingStatus] = mapped_column(
        _enum_col(BookingStatus, "booking_status"),
        nullable=False,
        default=BookingStatus.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    resident: Mapped["Resident"] = relationship(
        back_populates="bookings",
        foreign_keys=[resident_id],
        primaryjoin="Booking.resident_id == Resident.id",
    )
    pet: Mapped["Pet"] = relationship(
        back_populates="bookings",
        foreign_keys=[pet_id],
        primaryjoin="Booking.pet_id == Pet.id",
        viewonly=False,
    )
    caregiver: Mapped["Caregiver | None"] = relationship(
        back_populates="bookings",
        foreign_keys=[caregiver_id],
        primaryjoin="Booking.caregiver_id == Caregiver.id",
    )
