"""add company discovery runs

Revision ID: f3a9c7e1b420
Revises: e4b7c1d9a620
Create Date: 2026-09-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a9c7e1b420"
down_revision: Union[str, Sequence[str], None] = "e4b7c1d9a620"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "company_discovery_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default="queued",
            nullable=False,
        ),
        sa.Column(
            "scope",
            sa.String(length=30),
            server_default="unprofiled",
            nullable=False,
        ),
        sa.Column("query_budget", sa.Integer(), nullable=False),
        sa.Column(
            "query_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "total_count",
            sa.Integer(),
            server_default="0",
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'pause_requested', "
            "'paused', 'succeeded', 'failed')",
            name="ck_company_discovery_runs_status",
        ),
        sa.CheckConstraint(
            "scope = 'unprofiled'",
            name="ck_company_discovery_runs_scope",
        ),
        sa.CheckConstraint(
            "query_budget BETWEEN 3 AND 2200",
            name="ck_company_discovery_runs_query_budget",
        ),
        sa.CheckConstraint(
            "query_count BETWEEN 0 AND query_budget",
            name="ck_company_discovery_runs_query_count",
        ),
        sa.CheckConstraint(
            "total_count BETWEEN 0 AND 1000",
            name="ck_company_discovery_runs_total_count",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR char_length(btrim(error_code)) > 0",
            name="ck_company_discovery_runs_error_not_blank",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_company_discovery_runs_created",
        "company_discovery_runs",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "uq_company_discovery_runs_active",
        "company_discovery_runs",
        ["scope"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('queued', 'running', 'pause_requested', 'paused')"
        ),
    )
    op.create_table(
        "company_discovery_run_items",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("profile_status", sa.String(length=30), nullable=True),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "position BETWEEN 1 AND 1000",
            name="ck_company_discovery_run_items_position",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_company_discovery_run_items_status",
        ),
        sa.CheckConstraint(
            "profile_status IS NULL OR profile_status IN "
            "('candidate_found', 'verified', 'needs_review', 'not_found')",
            name="ck_company_discovery_run_items_profile_status",
        ),
        sa.CheckConstraint(
            "attempt_count BETWEEN 0 AND 3",
            name="ck_company_discovery_run_items_attempt_count",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR char_length(btrim(error_code)) > 0",
            name="ck_company_discovery_run_items_error_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["company_discovery_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", "company_id"),
        sa.UniqueConstraint(
            "run_id",
            "position",
            name="uq_company_discovery_run_items_position",
        ),
    )
    op.create_index(
        "ix_company_discovery_run_items_queue",
        "company_discovery_run_items",
        ["run_id", "status", "position"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_company_discovery_run_items_queue",
        table_name="company_discovery_run_items",
    )
    op.drop_table("company_discovery_run_items")
    op.drop_index(
        "uq_company_discovery_runs_active",
        table_name="company_discovery_runs",
        postgresql_where=sa.text(
            "status IN ('queued', 'running', 'pause_requested', 'paused')"
        ),
    )
    op.drop_index(
        "ix_company_discovery_runs_created",
        table_name="company_discovery_runs",
    )
    op.drop_table("company_discovery_runs")
