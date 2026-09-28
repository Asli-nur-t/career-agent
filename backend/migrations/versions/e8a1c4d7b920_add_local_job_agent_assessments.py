"""add local job agent assessments

Revision ID: e8a1c4d7b920
Revises: d4f8b2a7c913
Create Date: 2026-09-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "e8a1c4d7b920"
down_revision: Union[str, Sequence[str], None] = "d4f8b2a7c913"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "candidate_profiles",
        sa.Column(
            "internship_months",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_candidate_profiles_internship_months",
        "candidate_profiles",
        "internship_months BETWEEN 0 AND 120",
    )

    op.create_table(
        "job_candidate_assessments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("stars", sa.Integer(), nullable=False),
        sa.Column("recommendation", sa.String(length=30), nullable=False),
        sa.Column("confidence", sa.String(length=20), nullable=False),
        sa.Column("required_experience_min", sa.Integer(), nullable=True),
        sa.Column("experience_gap", sa.Integer(), nullable=True),
        sa.Column(
            "matched_requirements",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "missing_requirements",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "preferred_requirements",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "hard_blockers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("summary", sa.String(length=1000), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=50), nullable=False),
        sa.Column("job_content_hash", sa.String(length=64), nullable=False),
        sa.Column("profile_hash", sa.String(length=64), nullable=False),
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
            "score BETWEEN 0 AND 100",
            name="ck_job_candidate_assessments_score",
        ),
        sa.CheckConstraint(
            "stars BETWEEN 1 AND 5",
            name="ck_job_candidate_assessments_stars",
        ),
        sa.CheckConstraint(
            "recommendation IN ('strong_apply', 'apply', 'review', 'skip')",
            name="ck_job_candidate_assessments_recommendation",
        ),
        sa.CheckConstraint(
            "confidence IN ('low', 'medium', 'high')",
            name="ck_job_candidate_assessments_confidence",
        ),
        sa.CheckConstraint(
            "required_experience_min IS NULL OR "
            "required_experience_min BETWEEN 0 AND 50",
            name="ck_job_candidate_assessments_required_experience",
        ),
        sa.CheckConstraint(
            "experience_gap IS NULL OR experience_gap BETWEEN 0 AND 50",
            name="ck_job_candidate_assessments_experience_gap",
        ),
        sa.CheckConstraint(
            "char_length(job_content_hash) = 64",
            name="ck_job_candidate_assessments_job_hash",
        ),
        sa.CheckConstraint(
            "char_length(profile_hash) = 64",
            name="ck_job_candidate_assessments_profile_hash",
        ),
        sa.CheckConstraint(
            "char_length(btrim(summary)) > 0",
            name="ck_job_candidate_assessments_summary",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["job_board_candidates.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["candidate_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "candidate_id",
            name="uq_job_candidate_assessments_profile_candidate",
        ),
    )
    op.create_index(
        "ix_job_candidate_assessments_profile_score",
        "job_candidate_assessments",
        ["profile_id", "score"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_job_candidate_assessments_profile_score",
        table_name="job_candidate_assessments",
    )
    op.drop_table("job_candidate_assessments")
    op.drop_constraint(
        "ck_candidate_profiles_internship_months",
        "candidate_profiles",
        type_="check",
    )
    op.drop_column("candidate_profiles", "internship_months")
