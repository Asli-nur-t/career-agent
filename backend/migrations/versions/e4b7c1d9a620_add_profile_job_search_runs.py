"""add profile job search runs

Revision ID: e4b7c1d9a620
Revises: d91f4a6b2c73
Create Date: 2026-09-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e4b7c1d9a620"
down_revision: Union[str, Sequence[str], None] = "d91f4a6b2c73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "profile_job_search_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="queued",
            nullable=False,
        ),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_profile_job_search_runs_status",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR char_length(btrim(error_code)) > 0",
            name="ck_profile_job_search_runs_error_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["candidate_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_profile_job_search_runs_profile_created",
        "profile_job_search_runs",
        ["profile_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_profile_job_search_runs_active_profile",
        "profile_job_search_runs",
        ["profile_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_profile_job_search_runs_active_profile",
        table_name="profile_job_search_runs",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.drop_index(
        "ix_profile_job_search_runs_profile_created",
        table_name="profile_job_search_runs",
    )
    op.drop_table("profile_job_search_runs")
