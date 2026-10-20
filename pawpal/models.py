"""SQLAlchemy ORM models.

Ownership rule (enforced in the database, not just in Python):
``bookings (pet_id, resident_id)`` is a composite foreign key onto
``pets (id, resident_id)``. A booking therefore cannot reference a pet that
belongs to a different resident, even if someone bypasses the service layer.
"""
from __future__ import annotations

import enum
from datetime import date, datetime, time, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    Time,
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
    DRAFT = "draft"
    QUOTED = "quoted"
    PENDING_RESIDENT_APPROVAL = "pending_resident_approval"
    PENDING_ADMIN_APPROVAL = "pending_admin_approval"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    NEEDS_RESCHEDULING = "needs_rescheduling"
    # Week 1 called an unquoted, unscheduled booking "pending". It is now an alias
    # of DRAFT (same value), and migration 0002 rewrites stored 'pending' rows.
    PENDING = "draft"


class VisitStatus(str, enum.Enum):
    UNASSIGNED = "unassigned"
    PROPOSED = "proposed"      # recommended by the scheduler or a manager, not yet confirmed
    CONFIRMED = "confirmed"    # booking confirmed: the caregiver's time is held
    COMPLETED = "completed"
    CANCELLED = "cancelled"


ALLOWED_VISIT_MINUTES = (30, 60)


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
    pet_types: Mapped[list["CaregiverPetType"]] = relationship(cascade="all, delete-orphan")
    services: Mapped[list["CaregiverService"]] = relationship(cascade="all, delete-orphan")
    buildings: Mapped[list["CaregiverBuilding"]] = relationship(cascade="all, delete-orphan")

    @property
    def species_set(self) -> set[str]:
        return {p.species for p in self.pet_types}

    @property
    def service_set(self) -> set["ServiceType"]:
        return {s.service_type for s in self.services}

    @property
    def building_set(self) -> set[str]:
        return {b.building_name for b in self.buildings}


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
        CheckConstraint(
            "visit_duration_minutes IN (30, 60)", name="ck_booking_visit_duration"
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
    # ---- Week 2: scheduling, pricing and approvals
    visit_duration_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default="30"
    )
    additional_services: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    resident_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resident_approved_by: Mapped[int | None] = mapped_column(ForeignKey("residents.id"))
    admin_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admin_approved_by: Mapped[int | None] = mapped_column(ForeignKey("property_managers.id"))
    # Optimistic-locking counter: an UPDATE fails if someone else changed the row first.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    __mapper_args__ = {"version_id_col": version}

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
    visits: Mapped[list["Visit"]] = relationship(
        back_populates="booking",
        order_by="[Visit.visit_date, Visit.slot]",
        cascade="all, delete-orphan",
    )
    time_windows: Mapped[list["BookingTimeWindow"]] = relationship(
        back_populates="booking",
        order_by="BookingTimeWindow.slot",
        cascade="all, delete-orphan",
    )
    quotes: Mapped[list["BookingQuote"]] = relationship(
        back_populates="booking", order_by="BookingQuote.id", cascade="all, delete-orphan"
    )
    events: Mapped[list["BookingEvent"]] = relationship(
        back_populates="booking", order_by="BookingEvent.id", cascade="all, delete-orphan"
    )

    @property
    def current_quote(self) -> "BookingQuote | None":
        return next((q for q in reversed(self.quotes) if q.is_current), None)

    @property
    def num_days(self) -> int:
        return (self.end_date - self.start_date).days + 1


# =========================================================== Week 2 models
class PropertyManager(Base):
    """Approves caregiver assignments. building_name NULL = manages every building."""

    __tablename__ = "property_managers"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    building_name: Mapped[str | None] = mapped_column(String(120))

    def manages(self, building_name: str) -> bool:
        return self.building_name is None or self.building_name == building_name


class CaregiverPetType(Base):
    __tablename__ = "caregiver_pet_types"
    __table_args__ = (UniqueConstraint("caregiver_id", "species", name="uq_caregiver_species"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    caregiver_id: Mapped[int] = mapped_column(ForeignKey("caregivers.id"), nullable=False, index=True)
    species: Mapped[str] = mapped_column(String(40), nullable=False)  # lower-case


class CaregiverService(Base):
    __tablename__ = "caregiver_services"
    __table_args__ = (UniqueConstraint("caregiver_id", "service_type", name="uq_caregiver_service"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    caregiver_id: Mapped[int] = mapped_column(ForeignKey("caregivers.id"), nullable=False, index=True)
    service_type: Mapped[ServiceType] = mapped_column(
        _enum_col(ServiceType, "caregiver_service_type"), nullable=False
    )


class CaregiverBuilding(Base):
    """Buildings a caregiver covers (service area)."""

    __tablename__ = "caregiver_buildings"
    __table_args__ = (UniqueConstraint("caregiver_id", "building_name", name="uq_caregiver_building"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    caregiver_id: Mapped[int] = mapped_column(ForeignKey("caregivers.id"), nullable=False, index=True)
    building_name: Mapped[str] = mapped_column(String(120), nullable=False)


class CaregiverAvailability(Base):
    """A block of working hours on a specific date. Several blocks per day are allowed."""

    __tablename__ = "caregiver_availability"
    __table_args__ = (
        UniqueConstraint("caregiver_id", "work_date", "start_time", name="uq_availability_block"),
        CheckConstraint("end_time > start_time", name="ck_availability_time_order"),
        Index("ix_availability_caregiver_date", "caregiver_id", "work_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    caregiver_id: Mapped[int] = mapped_column(ForeignKey("caregivers.id"), nullable=False)
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)


class CaregiverTimeOff(Base):
    __tablename__ = "caregiver_time_off"
    __table_args__ = (CheckConstraint("end_date >= start_date", name="ck_time_off_date_order"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    caregiver_id: Mapped[int] = mapped_column(ForeignKey("caregivers.id"), nullable=False, index=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class BookingTimeWindow(Base):
    """Resident's preferred time window for the N-th visit of each day."""

    __tablename__ = "booking_time_windows"
    __table_args__ = (
        UniqueConstraint("booking_id", "slot", name="uq_time_window_slot"),
        CheckConstraint("end_time > start_time", name="ck_time_window_order"),
        CheckConstraint(f"slot BETWEEN 1 AND {MAX_VISITS_PER_DAY}", name="ck_time_window_slot"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), nullable=False, index=True)
    slot: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)

    booking: Mapped["Booking"] = relationship(back_populates="time_windows")


class Visit(Base):
    """One scheduled visit. Times are local building time (no time zone)."""

    __tablename__ = "visits"
    __table_args__ = (
        UniqueConstraint("booking_id", "visit_date", "slot", name="uq_visit_booking_date_slot"),
        CheckConstraint("end_time > start_time", name="ck_visit_time_order"),
        CheckConstraint("window_end > window_start", name="ck_visit_window_order"),
        CheckConstraint("duration_minutes IN (30, 60)", name="ck_visit_duration"),
        Index("ix_visits_caregiver_date", "caregiver_id", "visit_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), nullable=False, index=True)
    caregiver_id: Mapped[int | None] = mapped_column(ForeignKey("caregivers.id"))
    visit_date: Mapped[date] = mapped_column(Date, nullable=False)
    slot: Mapped[int] = mapped_column(Integer, nullable=False)
    window_start: Mapped[time] = mapped_column(Time, nullable=False)
    window_end: Mapped[time] = mapped_column(Time, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    service_type: Mapped[ServiceType] = mapped_column(
        _enum_col(ServiceType, "visit_service_type"), nullable=False
    )
    assignment_status: Mapped[VisitStatus] = mapped_column(
        _enum_col(VisitStatus, "visit_status"), nullable=False, default=VisitStatus.UNASSIGNED
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    booking: Mapped["Booking"] = relationship(back_populates="visits")
    caregiver: Mapped["Caregiver | None"] = relationship()


class BookingQuote(Base):
    """An immutable price quote. Money is stored in integer cents."""

    __tablename__ = "booking_quotes"

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), nullable=False, index=True)
    pricing_version: Mapped[str] = mapped_column(String(40), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    visit_subtotal_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    additional_fees_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    line_items: Mapped[list] = mapped_column(JSON, nullable=False)
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    booking: Mapped["Booking"] = relationship(back_populates="quotes")


class BookingEvent(Base):
    """Audit trail: every status change with who did it and when."""

    __tablename__ = "booking_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    booking: Mapped["Booking"] = relationship(back_populates="events")
