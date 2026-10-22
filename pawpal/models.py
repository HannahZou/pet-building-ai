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
    # Week 4: demo/synthetic residents (and everything they book) are kept apart in analytics.
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

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
    # Week 3: medical notes are hidden from managers unless explicitly authorised.
    can_view_medical_notes: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )

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



# =========================================================== Week 3 models
class ExecutionStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    MISSED = "missed"
    CANCELLED = "cancelled"
    NEEDS_REVIEW = "needs_review"


class ActivityType(str, enum.Enum):
    FEEDING = "feeding"
    FRESH_WATER = "fresh_water"
    DOG_WALKING = "dog_walking"
    LITTER_BOX = "litter_box"
    PLAYTIME = "playtime"
    WELLNESS_OBSERVATION = "wellness_observation"
    OTHER = "other"


class CompletionStatus(str, enum.Enum):
    COMPLETED = "completed"
    NOT_COMPLETED = "not_completed"   # attempted / could not be done
    SKIPPED = "skipped"               # deliberately not done (e.g. resident asked)
    NOT_RECORDED = "not_recorded"     # caregiver left without recording it (set at check-out)


class PhotoVisibility(str, enum.Enum):
    VISIBLE = "visible"   # resident, assigned caregiver and managers
    HIDDEN = "hidden"     # hidden from the resident by a manager


class ReportStatus(str, enum.Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class NotificationType(str, enum.Enum):
    VISIT_STARTED = "visit_started"
    VISIT_COMPLETED = "visit_completed"
    DAILY_REPORT_AVAILABLE = "daily_report_available"
    MISSED_VISIT = "missed_visit"
    CARE_ISSUE = "care_issue"


class NotificationChannel(str, enum.Enum):
    IN_APP = "in_app"
    EMAIL = "email"


class DeliveryStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class BuildingSetting(Base):
    """Per-building settings; the time zone is used to group visits into local days."""

    __tablename__ = "building_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    building_name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)


class VisitExecution(Base):
    """What actually happened at a scheduled visit (one row per visit)."""

    __tablename__ = "visit_executions"
    __table_args__ = (
        CheckConstraint(
            "actual_check_out IS NULL OR (actual_check_in IS NOT NULL AND actual_check_out > actual_check_in)",
            name="ck_execution_checkout_after_checkin",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scheduled_visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id"), nullable=False, unique=True)
    caregiver_id: Mapped[int] = mapped_column(ForeignKey("caregivers.id"), nullable=False, index=True)
    actual_check_in: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_check_out: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_status: Mapped[ExecutionStatus] = mapped_column(
        _enum_col(ExecutionStatus, "execution_status"), nullable=False, default=ExecutionStatus.SCHEDULED
    )
    review_reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("property_managers.id"))
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    __mapper_args__ = {"version_id_col": version}

    visit: Mapped["Visit"] = relationship()
    caregiver: Mapped["Caregiver"] = relationship()

    @property
    def duration_minutes(self) -> int | None:
        if self.actual_check_in and self.actual_check_out:
            def utc(dt: datetime) -> datetime:  # SQLite returns naive UTC
                return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
            return int((utc(self.actual_check_out) - utc(self.actual_check_in)).total_seconds() // 60)
        return None


class CareActivityLog(Base):
    __tablename__ = "care_activity_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    scheduled_visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id"), nullable=False, index=True)
    caregiver_id: Mapped[int] = mapped_column(ForeignKey("caregivers.id"), nullable=False)
    activity_type: Mapped[ActivityType] = mapped_column(_enum_col(ActivityType, "activity_type"), nullable=False)
    completion_status: Mapped[CompletionStatus] = mapped_column(
        _enum_col(CompletionStatus, "completion_status"), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)
    flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    visit: Mapped["Visit"] = relationship()


class PetCarePhoto(Base):
    __tablename__ = "pet_care_photos"

    id: Mapped[int] = mapped_column(primary_key=True)
    scheduled_visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id"), nullable=False, index=True)
    caregiver_id: Mapped[int] = mapped_column(ForeignKey("caregivers.id"), nullable=False)
    file_path: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)  # storage key, never shown
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    caption: Mapped[str | None] = mapped_column(String(300))
    visibility_status: Mapped[PhotoVisibility] = mapped_column(
        _enum_col(PhotoVisibility, "photo_visibility"), nullable=False, default=PhotoVisibility.VISIBLE
    )
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    visit: Mapped["Visit"] = relationship()


class DailyCareReport(Base):
    """One report per booking per local calendar day. AI text is published only after approval."""

    __tablename__ = "daily_care_reports"
    __table_args__ = (UniqueConstraint("booking_id", "report_date", name="uq_report_booking_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), nullable=False, index=True)
    pet_id: Mapped[int] = mapped_column(ForeignKey("pets.id"), nullable=False, index=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    report_text: Mapped[str] = mapped_column(Text, nullable=False)
    structured_output: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_refs: Mapped[dict] = mapped_column(JSON, nullable=False)
    evaluation: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    model_identifier: Mapped[str] = mapped_column(String(80), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    needs_attention: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_status: Mapped[ReportStatus] = mapped_column(
        _enum_col(ReportStatus, "report_status"), nullable=False, default=ReportStatus.PENDING_REVIEW
    )
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("property_managers.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_notes: Mapped[str | None] = mapped_column(Text)
    generation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    booking: Mapped["Booking"] = relationship()
    pet: Mapped["Pet"] = relationship()


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    resident_id: Mapped[int] = mapped_column(ForeignKey("residents.id"), nullable=False, index=True)
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("bookings.id"), index=True)
    notification_type: Mapped[NotificationType] = mapped_column(
        _enum_col(NotificationType, "notification_type"), nullable=False
    )
    channel: Mapped[NotificationChannel] = mapped_column(
        _enum_col(NotificationChannel, "notification_channel"), nullable=False
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    delivery_status: Mapped[DeliveryStatus] = mapped_column(
        _enum_col(DeliveryStatus, "delivery_status"), nullable=False, default=DeliveryStatus.PENDING
    )
    provider: Mapped[str | None] = mapped_column(String(40))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    """Generic audit trail. ``details`` holds ids and statuses only - no notes or medical data."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)



# =========================================================== Week 4 models
class PlatformAdmin(Base):
    """Platform staff with explicitly granted permissions (see ``pawpal.access``)."""

    __tablename__ = "platform_admins"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    permissions: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default="[]")


class EvaluationRun(Base):
    """A stored run of an AI evaluation (booking extraction or care reports)."""

    __tablename__ = "evaluation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    system: Mapped[str] = mapped_column(String(80), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(80), nullable=False)
    dataset_size: Mapped[int] = mapped_column(Integer, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    failures: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
