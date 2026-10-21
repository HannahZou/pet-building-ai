"""Week 3: visit execution, activity logs, photos, daily reports, notifications, audit.

* New tables: building_settings, visit_executions, care_activity_logs,
  pet_care_photos, daily_care_reports, notifications, audit_events.
* property_managers.can_view_medical_notes (default false).
* Backfill: one time-zone row per existing building (PAWPAL_DEFAULT_TIMEZONE,
  default America/New_York) and a 'scheduled' execution row for every visit that
  is already confirmed. No existing row is modified or removed.

Revision ID: 0003_week3
Revises: 0002_week2
Create Date: 2026-10-20
"""
import os

import sqlalchemy as sa
from alembic import op

revision = "0003_week3"
down_revision = "0002_week2"
branch_labels = None
depends_on = None

EXEC = "('scheduled', 'in_progress', 'completed', 'missed', 'cancelled', 'needs_review')"
ACTIVITY = "('feeding', 'fresh_water', 'dog_walking', 'litter_box', 'playtime', 'wellness_observation', 'other')"
COMPLETION = "('completed', 'not_completed', 'skipped', 'not_recorded')"
NOTIF = "('visit_started', 'visit_completed', 'daily_report_available', 'missed_visit', 'care_issue')"


def upgrade() -> None:
    op.add_column("property_managers",  # plain ADD COLUMN: no table rebuild
                  sa.Column("can_view_medical_notes", sa.Boolean, nullable=False, server_default="0"))

    op.create_table(
        "building_settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("building_name", sa.String(120), nullable=False, unique=True),
        sa.Column("timezone", sa.String(64), nullable=False),
    )

    op.create_table(
        "visit_executions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("scheduled_visit_id", sa.Integer, sa.ForeignKey("visits.id"), nullable=False, unique=True),
        sa.Column("caregiver_id", sa.Integer, sa.ForeignKey("caregivers.id"), nullable=False),
        sa.Column("actual_check_in", sa.DateTime(timezone=True)),
        sa.Column("actual_check_out", sa.DateTime(timezone=True)),
        sa.Column("execution_status", sa.String(32), nullable=False),
        sa.Column("review_reasons", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.Integer, sa.ForeignKey("property_managers.id")),
        sa.Column("review_note", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.CheckConstraint(
            "actual_check_out IS NULL OR (actual_check_in IS NOT NULL AND actual_check_out > actual_check_in)",
            name="ck_execution_checkout_after_checkin",
        ),
        sa.CheckConstraint(f"execution_status IN {EXEC}", name="execution_status"),
    )
    op.create_index("ix_visit_executions_caregiver_id", "visit_executions", ["caregiver_id"])

    op.create_table(
        "care_activity_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("scheduled_visit_id", sa.Integer, sa.ForeignKey("visits.id"), nullable=False),
        sa.Column("caregiver_id", sa.Integer, sa.ForeignKey("caregivers.id"), nullable=False),
        sa.Column("activity_type", sa.String(32), nullable=False),
        sa.Column("completion_status", sa.String(32), nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("flagged", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"activity_type IN {ACTIVITY}", name="activity_type"),
        sa.CheckConstraint(f"completion_status IN {COMPLETION}", name="completion_status"),
    )
    op.create_index("ix_care_activity_logs_scheduled_visit_id", "care_activity_logs", ["scheduled_visit_id"])

    op.create_table(
        "pet_care_photos",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("scheduled_visit_id", sa.Integer, sa.ForeignKey("visits.id"), nullable=False),
        sa.Column("caregiver_id", sa.Integer, sa.ForeignKey("caregivers.id"), nullable=False),
        sa.Column("file_path", sa.String(255), nullable=False, unique=True),
        sa.Column("content_type", sa.String(32), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("width", sa.Integer, nullable=False),
        sa.Column("height", sa.Integer, nullable=False),
        sa.Column("caption", sa.String(300)),
        sa.Column("visibility_status", sa.String(32), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("visibility_status IN ('visible', 'hidden')", name="photo_visibility"),
    )
    op.create_index("ix_pet_care_photos_scheduled_visit_id", "pet_care_photos", ["scheduled_visit_id"])

    op.create_table(
        "daily_care_reports",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("booking_id", sa.Integer, sa.ForeignKey("bookings.id"), nullable=False),
        sa.Column("pet_id", sa.Integer, sa.ForeignKey("pets.id"), nullable=False),
        sa.Column("report_date", sa.Date, nullable=False),
        sa.Column("report_text", sa.Text, nullable=False),
        sa.Column("structured_output", sa.JSON, nullable=False),
        sa.Column("source_refs", sa.JSON, nullable=False),
        sa.Column("evaluation", sa.JSON, nullable=False),
        sa.Column("model_identifier", sa.String(80), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("needs_attention", sa.Boolean, nullable=False),
        sa.Column("review_status", sa.String(32), nullable=False),
        sa.Column("reviewed_by", sa.Integer, sa.ForeignKey("property_managers.id")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_notes", sa.Text),
        sa.Column("generation_count", sa.Integer, nullable=False),
        sa.UniqueConstraint("booking_id", "report_date", name="uq_report_booking_date"),
        sa.CheckConstraint("review_status IN ('pending_review', 'approved', 'rejected')", name="report_status"),
    )
    op.create_index("ix_daily_care_reports_booking_id", "daily_care_reports", ["booking_id"])
    op.create_index("ix_daily_care_reports_pet_id", "daily_care_reports", ["pet_id"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("resident_id", sa.Integer, sa.ForeignKey("residents.id"), nullable=False),
        sa.Column("booking_id", sa.Integer, sa.ForeignKey("bookings.id")),
        sa.Column("notification_type", sa.String(32), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
        sa.Column("delivery_status", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(40)),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("last_error", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(f"notification_type IN {NOTIF}", name="notification_type"),
        sa.CheckConstraint("channel IN ('in_app', 'email')", name="notification_channel"),
        sa.CheckConstraint("delivery_status IN ('pending', 'sent', 'failed')", name="delivery_status"),
    )
    op.create_index("ix_notifications_resident_id", "notifications", ["resident_id"])
    op.create_index("ix_notifications_booking_id", "notifications", ["booking_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.Column("entity_id", sa.Integer, nullable=False),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("actor_role", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.Integer),
        sa.Column("details", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_entity", "audit_events", ["entity_type", "entity_id"])

    # ---- backfill (inserts only)
    tz = os.getenv("PAWPAL_DEFAULT_TIMEZONE", "America/New_York")
    op.get_bind().execute(
        sa.text("INSERT INTO building_settings (building_name, timezone) "
                "SELECT DISTINCT building_name, :tz FROM residents"),
        {"tz": tz},
    )
    op.execute(
        "INSERT INTO visit_executions (scheduled_visit_id, caregiver_id, execution_status, review_reasons, "
        "created_at, updated_at, version) "
        "SELECT id, caregiver_id, 'scheduled', '[]', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1 FROM visits "
        "WHERE assignment_status = 'confirmed' AND caregiver_id IS NOT NULL"
    )


def downgrade() -> None:
    for table in ("audit_events", "notifications", "daily_care_reports", "pet_care_photos",
                  "care_activity_logs", "visit_executions", "building_settings"):
        op.drop_table(table)
    if op.get_bind().dialect.name == "sqlite":
        # Native DROP COLUMN (SQLite >= 3.35): no table rebuild, so foreign keys from
        # bookings.admin_approved_by stay valid.
        op.execute("ALTER TABLE property_managers DROP COLUMN can_view_medical_notes")
    else:
        op.drop_column("property_managers", "can_view_medical_notes")
