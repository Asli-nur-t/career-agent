"""add job board candidates

Revision ID: 64f3a9c20b11
Revises: 37b3185839cd
Create Date: 2026-09-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "64f3a9c20b11"
down_revision: Union[str, Sequence[str], None] = "37b3185839cd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "job_board_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("external_id", sa.String(length=500), nullable=False),
        sa.Column("listing_url", sa.String(length=2048), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("company_name_raw", sa.String(length=500), nullable=True),
        sa.Column("location", sa.String(length=500), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default="needs_review",
            nullable=False,
        ),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "provider IN "
            "('linkedin', 'kariyer', 'indeed', 'glassdoor')",
            name="ck_job_board_candidates_provider",
        ),
        sa.CheckConstraint(
            "char_length(btrim(external_id)) > 0",
            name="ck_job_board_candidates_external_id_not_blank",
        ),
        sa.CheckConstraint(
            "char_length(btrim(listing_url)) > 0",
            name="ck_job_board_candidates_url_not_blank",
        ),
        sa.CheckConstraint(
            "char_length(btrim(title)) > 0",
            name="ck_job_board_candidates_title_not_blank",
        ),
        sa.CheckConstraint(
            "company_id IS NOT NULL OR (company_name_raw IS NOT NULL AND "
            "char_length(btrim(company_name_raw)) > 0)",
            name="ck_job_board_candidates_company_identity",
        ),
        sa.CheckConstraint(
            "status IN ('needs_review', 'approved', 'rejected')",
            name="ck_job_board_candidates_status",
        ),
        sa.CheckConstraint(
            "status <> 'approved' OR company_id IS NOT NULL",
            name="ck_job_board_candidates_approval_company",
        ),
        sa.CheckConstraint(
            "(status = 'approved' AND approved_at IS NOT NULL) OR "
            "(status <> 'approved' AND approved_at IS NULL)",
            name="ck_job_board_candidates_approval_time",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "external_id",
            name="uq_job_board_candidates_provider_external_id",
        ),
    )
    op.create_index(
        "ix_job_board_candidates_status_last_seen",
        "job_board_candidates",
        ["status", "last_seen_at"],
        unique=False,
    )
    op.create_index(
        "ix_job_board_candidates_company_status",
        "job_board_candidates",
        ["company_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_job_board_candidates_company_status",
        table_name="job_board_candidates",
    )
    op.drop_index(
        "ix_job_board_candidates_status_last_seen",
        table_name="job_board_candidates",
    )
    op.drop_table("job_board_candidates")
