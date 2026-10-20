"""Week 1 baseline schema (residents, pets, caregivers, bookings).

Frozen copy of the schema Week 1 created with ``create_all``. Existing Week 1
databases are *stamped* at this revision (not re-created), then upgraded.

Revision ID: 0001_week1
Revises:
Create Date: 2026-10-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0001_week1"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "residents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("building_name", sa.String(120), nullable=False),
        sa.Column("apartment_number", sa.String(20), nullable=False),
        sa.Column("phone", sa.String(40)),
    )
    op.create_table(
        "caregivers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("phone", sa.String(40)),
        sa.Column("active_status", sa.Boolean, nullable=False),
    )
    op.create_table(
        "pets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("resident_id", sa.Integer, sa.ForeignKey("residents.id"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("species", sa.String(40), nullable=False),
        sa.Column("breed", sa.String(80)),
        sa.Column("age", sa.Integer),
        sa.Column("feeding_instructions", sa.Text),
        sa.Column("medical_notes", sa.Text),
        sa.UniqueConstraint("id", "resident_id", name="uq_pet_id_resident_id"),
        sa.CheckConstraint("age IS NULL OR age >= 0", name="ck_pet_age_nonnegative"),
    )
    op.create_index("ix_pets_resident_id", "pets", ["resident_id"])
    op.create_table(
        "bookings",
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
        sa.CheckConstraint(
            "service_type IN ('feeding', 'walking', 'check_in', 'full_care')", name="service_type"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'completed', 'cancelled')", name="booking_status"
        ),
    )
    op.create_index("ix_bookings_resident_id", "bookings", ["resident_id"])
    op.create_index("ix_bookings_pet_id", "bookings", ["pet_id"])


def downgrade() -> None:
    for table in ("bookings", "pets", "caregivers", "residents"):
        op.drop_table(table)
