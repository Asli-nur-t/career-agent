"""add job postings

Revision ID: 72c40d9e9f81
Revises: 1301e7dd3cd0
Create Date: 2026-09-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "72c40d9e9f81"
down_revision: Union[str, Sequence[str], None] = "1301e7dd3cd0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "job_postings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("career_source_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(length=500), nullable=False),
        sa.Column("job_url", sa.String(length=2048), nullable=False),
        sa.Column("apply_url", sa.String(length=2048), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("location", sa.String(length=500), nullable=True),
        sa.Column("department", sa.String(length=300), nullable=True),
        sa.Column("employment_type", sa.String(length=100), nullable=True),
        sa.Column("description_text", sa.Text(), nullable=True),
        sa.Column("is_remote", sa.Boolean(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="active",
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
        sa.Column(
            "last_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
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
            "char_length(btrim(external_id)) > 0",
            name="ck_job_postings_external_id_not_blank",
        ),
        sa.CheckConstraint(
            "char_length(btrim(title)) > 0",
            name="ck_job_postings_title_not_blank",
        ),
        sa.CheckConstraint(
            "char_length(btrim(job_url)) > 0",
            name="ck_job_postings_url_not_blank",
        ),
        sa.CheckConstraint(
            "char_length(content_hash) = 64",
            name="ck_job_postings_content_hash_length",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'closed')",
            name="ck_job_postings_status",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND closed_at IS NULL) OR "
            "(status = 'closed' AND closed_at IS NOT NULL)",
            name="ck_job_postings_closed_state",
        ),
        sa.ForeignKeyConstraint(
            ["career_source_id"],
            ["career_sources.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "career_source_id",
            "external_id",
            name="uq_job_postings_source_external_id",
        ),
    )
    op.create_index(
        "ix_job_postings_source_status",
        "job_postings",
        ["career_source_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_job_postings_status_last_seen",
        "job_postings",
        ["status", "last_seen_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_job_postings_status_last_seen",
        table_name="job_postings",
    )
    op.drop_index(
        "ix_job_postings_source_status",
        table_name="job_postings",
    )
    op.drop_table("job_postings")
