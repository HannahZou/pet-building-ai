"""Week 2: scheduling, pricing and approval workflow.

* bookings: new statuses ('pending' -> 'draft'), visit duration, add-on services,
  approval stamps and an optimistic-locking version column. SQLite cannot alter a
  CHECK constraint, so the table is rebuilt with the standard copy-and-rename
  procedure inside one transaction: every existing row and id is preserved.
* New tables: property_managers, caregiver_pet_types, caregiver_services,
  caregiver_buildings, caregiver_availability, caregiver_time_off,
  booking_time_windows, visits, booking_quotes, booking_events.
* SQLite triggers that make it impossible to store two overlapping *confirmed*
  visits for the same caregiver (the database-level double-booking guard).

Revision ID: 0002_week2
Revises: 0001_week1
Create Date: 2026-10-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_week2"
down_revision = "0001_week1"
branch_labels = None
depends_on = None

SERVICES = "('feeding', 'walking', 'check_in', 'full_care')"
NEW_STATUSES = (
    "('draft', 'quoted', 'pending_resident_approval', 'pending_admin_approval', "
    "'confirmed', 'completed', 'cancelled', 'rejected', 'needs_rescheduling')"
)
OLD_STATUSES = "('pending', 'confirmed', 'completed', 'cancelled')"
VISIT_STATUSES = "('unassigned', 'proposed', 'confirmed', 'completed', 'cancelled')"

COMMON_COLS = (
    "id, resident_id, pet_id, caregiver_id, start_date, end_date, visits_per_day, "
    "service_type, special_instructions, photo_updates_required, status, created_at"
)

OVERLAP_SQL = """
SELECT RAISE(ABORT, 'caregiver_double_booked')
WHERE EXISTS (
    SELECT 1 FROM visits v
    WHERE v.caregiver_id = NEW.caregiver_id
      AND v.assignment_status = 'confirmed'
      AND v.visit_date = NEW.visit_date
      AND v.id IS NOT NEW.id
      AND v.start_time < NEW.end_time
      AND NEW.start_time < v.end_time
);
"""


def _bookings_columns(new: bool) -> list:
    cols = [
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("resident_id", sa.Integer, sa.ForeignKey("residents.id"), nullable=False),
        sa.Column("pet_id", sa.Integer, nullable=False),
        sa.Column("caregiver_id", sa.Integer, sa.ForeignKey("caregivers.id")),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),
        sa.Column("visits_per_day", sa.Integer, nullable=False),
        sa.Column("service_type", sa.String(32), nullable=False),
        sa.Column("special_instructions", sa.Text),
        sa.Column("photo_updates_required", sa.Boolean, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["pet_id", "resident_id"], ["pets.id", "pets.resident_id"],
            name="fk_booking_pet_belongs_to_resident",
        ),
        sa.CheckConstraint("end_date >= start_date", name="ck_booking_date_order"),
        sa.CheckConstraint("visits_per_day BETWEEN 1 AND 6", name="ck_booking_visits_range"),
        sa.CheckConstraint(f"service_type IN {SERVICES}", name="service_type"),
        sa.CheckConstraint(
            f"status IN {NEW_STATUSES if new else OLD_STATUSES}", name="booking_status"
        ),
    ]
    if new:
        cols += [
            sa.Column("visit_duration_minutes", sa.Integer, nullable=False, server_default="30"),
            sa.Column("additional_services", sa.JSON, nullable=False, server_default="[]"),
            sa.Column("resident_approved_at", sa.DateTime(timezone=True)),
            sa.Column("resident_approved_by", sa.Integer, sa.ForeignKey("residents.id")),
            sa.Column("admin_approved_at", sa.DateTime(timezone=True)),
            sa.Column("admin_approved_by", sa.Integer, sa.ForeignKey("property_managers.id")),
            sa.Column("version", sa.Integer, nullable=False, server_default="1"),
            sa.CheckConstraint("visit_duration_minutes IN (30, 60)", name="ck_booking_visit_duration"),
        ]
    return cols


def _rebuild_bookings(new: bool, status_sql: str) -> None:
    op.create_table("bookings_rebuild", *_bookings_columns(new))
    op.execute(
        f"INSERT INTO bookings_rebuild ({COMMON_COLS}) "
        f"SELECT {COMMON_COLS.replace('status,', status_sql + ',')} FROM bookings"
    )
    op.drop_index("ix_bookings_resident_id", table_name="bookings")
    op.drop_index("ix_bookings_pet_id", table_name="bookings")
    op.drop_table("bookings")
    op.rename_table("bookings_rebuild", "bookings")
    op.create_index("ix_bookings_resident_id", "bookings", ["resident_id"])
    op.create_index("ix_bookings_pet_id", "bookings", ["pet_id"])


def upgrade() -> None:
    op.create_table(
        "property_managers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("building_name", sa.String(120)),
    )

    _rebuild_bookings(
        new=True, status_sql="CASE status WHEN 'pending' THEN 'draft' ELSE status END AS status"
    )

    op.create_table(
        "caregiver_pet_types",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("caregiver_id", sa.Integer, sa.ForeignKey("caregivers.id"), nullable=False),
        sa.Column("species", sa.String(40), nullable=False),
        sa.UniqueConstraint("caregiver_id", "species", name="uq_caregiver_species"),
    )
    op.create_index("ix_caregiver_pet_types_caregiver_id", "caregiver_pet_types", ["caregiver_id"])

    op.create_table(
        "caregiver_services",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("caregiver_id", sa.Integer, sa.ForeignKey("caregivers.id"), nullable=False),
        sa.Column("service_type", sa.String(32), nullable=False),
        sa.UniqueConstraint("caregiver_id", "service_type", name="uq_caregiver_service"),
        sa.CheckConstraint(f"service_type IN {SERVICES}", name="caregiver_service_type"),
    )
    op.create_index("ix_caregiver_services_caregiver_id", "caregiver_services", ["caregiver_id"])

    op.create_table(
        "caregiver_buildings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("caregiver_id", sa.Integer, sa.ForeignKey("caregivers.id"), nullable=False),
        sa.Column("building_name", sa.String(120), nullable=False),
        sa.UniqueConstraint("caregiver_id", "building_name", name="uq_caregiver_building"),
    )
    op.create_index("ix_caregiver_buildings_caregiver_id", "caregiver_buildings", ["caregiver_id"])

    op.create_table(
        "caregiver_availability",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("caregiver_id", sa.Integer, sa.ForeignKey("caregivers.id"), nullable=False),
        sa.Column("work_date", sa.Date, nullable=False),
        sa.Column("start_time", sa.Time, nullable=False),
        sa.Column("end_time", sa.Time, nullable=False),
        sa.UniqueConstraint("caregiver_id", "work_date", "start_time", name="uq_availability_block"),
        sa.CheckConstraint("end_time > start_time", name="ck_availability_time_order"),
    )
    op.create_index(
        "ix_availability_caregiver_date", "caregiver_availability", ["caregiver_id", "work_date"]
    )

    op.create_table(
        "caregiver_time_off",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("caregiver_id", sa.Integer, sa.ForeignKey("caregivers.id"), nullable=False),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),
        sa.Column("reason", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("end_date >= start_date", name="ck_time_off_date_order"),
    )
    op.create_index("ix_caregiver_time_off_caregiver_id", "caregiver_time_off", ["caregiver_id"])

    op.create_table(
        "booking_time_windows",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("booking_id", sa.Integer, sa.ForeignKey("bookings.id"), nullable=False),
        sa.Column("slot", sa.Integer, nullable=False),
        sa.Column("start_time", sa.Time, nullable=False),
        sa.Column("end_time", sa.Time, nullable=False),
        sa.UniqueConstraint("booking_id", "slot", name="uq_time_window_slot"),
        sa.CheckConstraint("end_time > start_time", name="ck_time_window_order"),
        sa.CheckConstraint("slot BETWEEN 1 AND 6", name="ck_time_window_slot"),
    )
    op.create_index("ix_booking_time_windows_booking_id", "booking_time_windows", ["booking_id"])

    op.create_table(
        "visits",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("booking_id", sa.Integer, sa.ForeignKey("bookings.id"), nullable=False),
        sa.Column("caregiver_id", sa.Integer, sa.ForeignKey("caregivers.id")),
        sa.Column("visit_date", sa.Date, nullable=False),
        sa.Column("slot", sa.Integer, nullable=False),
        sa.Column("window_start", sa.Time, nullable=False),
        sa.Column("window_end", sa.Time, nullable=False),
        sa.Column("start_time", sa.Time, nullable=False),
        sa.Column("end_time", sa.Time, nullable=False),
        sa.Column("duration_minutes", sa.Integer, nullable=False),
        sa.Column("service_type", sa.String(32), nullable=False),
        sa.Column("assignment_status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("booking_id", "visit_date", "slot", name="uq_visit_booking_date_slot"),
        sa.CheckConstraint("end_time > start_time", name="ck_visit_time_order"),
        sa.CheckConstraint("window_end > window_start", name="ck_visit_window_order"),
        sa.CheckConstraint("duration_minutes IN (30, 60)", name="ck_visit_duration"),
        sa.CheckConstraint(f"service_type IN {SERVICES}", name="visit_service_type"),
        sa.CheckConstraint(f"assignment_status IN {VISIT_STATUSES}", name="visit_status"),
    )
    op.create_index("ix_visits_booking_id", "visits", ["booking_id"])
    op.create_index("ix_visits_caregiver_date", "visits", ["caregiver_id", "visit_date"])

    op.create_table(
        "booking_quotes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("booking_id", sa.Integer, sa.ForeignKey("bookings.id"), nullable=False),
        sa.Column("pricing_version", sa.String(40), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("visit_subtotal_cents", sa.Integer, nullable=False),
        sa.Column("additional_fees_cents", sa.Integer, nullable=False),
        sa.Column("total_cents", sa.Integer, nullable=False),
        sa.Column("line_items", sa.JSON, nullable=False),
        sa.Column("inputs", sa.JSON, nullable=False),
        sa.Column("is_current", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_booking_quotes_booking_id", "booking_quotes", ["booking_id"])

    op.create_table(
        "booking_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("booking_id", sa.Integer, sa.ForeignKey("bookings.id"), nullable=False),
        sa.Column("from_status", sa.String(32)),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("actor_role", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.Integer),
        sa.Column("note", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_booking_events_booking_id", "booking_events", ["booking_id"])

    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "CREATE TRIGGER trg_visits_no_overlap_insert BEFORE INSERT ON visits "
            "WHEN NEW.caregiver_id IS NOT NULL AND NEW.assignment_status = 'confirmed' "
            f"BEGIN {OVERLAP_SQL} END"
        )
        op.execute(
            "CREATE TRIGGER trg_visits_no_overlap_update BEFORE UPDATE OF "
            "caregiver_id, assignment_status, visit_date, start_time, end_time ON visits "
            "WHEN NEW.caregiver_id IS NOT NULL AND NEW.assignment_status = 'confirmed' "
            f"BEGIN {OVERLAP_SQL} END"
        )
    # PostgreSQL: use an exclusion constraint instead (see README, "Production").


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_visits_no_overlap_insert")
        op.execute("DROP TRIGGER IF EXISTS trg_visits_no_overlap_update")
    for table in (
        "booking_events", "booking_quotes", "visits", "booking_time_windows",
        "caregiver_time_off", "caregiver_availability", "caregiver_buildings",
        "caregiver_services", "caregiver_pet_types",
    ):
        op.drop_table(table)
    _rebuild_bookings(
        new=False,
        status_sql=(
            "CASE WHEN status IN ('confirmed', 'completed', 'cancelled') THEN status "
            "WHEN status = 'rejected' THEN 'cancelled' ELSE 'pending' END AS status"
        ),
    )
    op.drop_table("property_managers")
