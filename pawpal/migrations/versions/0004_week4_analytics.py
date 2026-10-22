"""Week 4: analytics support.

* residents.is_synthetic - demo residents (``@example.test`` e-mails from the seed
  scripts) are flagged so analytics never mixes synthetic and real records.
* platform_admins - platform staff with explicit permissions.
* evaluation_runs - stored AI evaluation results (history in the UI).

Revision ID: 0004_week4
Revises: 0003_week3
Create Date: 2026-10-21
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_week4"
down_revision = "0003_week3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("residents", sa.Column("is_synthetic", sa.Boolean, nullable=False, server_default="0"))
    op.execute("UPDATE residents SET is_synthetic = 1 WHERE email LIKE '%@example.test'")

    op.create_table(
        "platform_admins",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("permissions", sa.JSON, nullable=False, server_default="[]"),
    )
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("system", sa.String(80), nullable=False),
        sa.Column("dataset_version", sa.String(80), nullable=False),
        sa.Column("dataset_size", sa.Integer, nullable=False),
        sa.Column("metrics", sa.JSON, nullable=False),
        sa.Column("failures", sa.JSON, nullable=False),
        sa.Column("config", sa.JSON, nullable=False),
        sa.Column("is_synthetic", sa.Boolean, nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_evaluation_runs_kind", "evaluation_runs", ["kind"])


def downgrade() -> None:
    op.drop_table("evaluation_runs")
    op.drop_table("platform_admins")
    if op.get_bind().dialect.name == "sqlite":
        op.execute("ALTER TABLE residents DROP COLUMN is_synthetic")
    else:
        op.drop_column("residents", "is_synthetic")
