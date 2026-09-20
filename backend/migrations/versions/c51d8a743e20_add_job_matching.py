"""add candidate profiles and job matches

Revision ID: c51d8a743e20
Revises: 9ad127e93f42
Create Date: 2026-09-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c51d8a743e20"
down_revision: Union[str, Sequence[str], None] = "9ad127e93f42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "candidate_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("target_roles", postgresql.JSONB(), nullable=False),
        sa.Column(
            "secondary_roles",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("skills", postgresql.JSONB(), nullable=False),
        sa.Column(
            "preferred_locations",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "excluded_keywords",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "max_years_experience",
            sa.Integer(),
            server_default="3",
            nullable=False,
        ),
        sa.Column(
            "remote_allowed",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
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
            "char_length(btrim(label)) > 0",
            name="ck_candidate_profiles_label_not_blank",
        ),
        sa.CheckConstraint(
            "char_length(config_hash) = 64",
            name="ck_candidate_profiles_hash_length",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(target_roles) = 'array'",
            name="ck_candidate_profiles_target_roles_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(secondary_roles) = 'array'",
            name="ck_candidate_profiles_secondary_roles_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(skills) = 'array'",
            name="ck_candidate_profiles_skills_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(preferred_locations) = 'array'",
            name="ck_candidate_profiles_locations_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(excluded_keywords) = 'array'",
            name="ck_candidate_profiles_excluded_array",
        ),
        sa.CheckConstraint(
            "max_years_experience BETWEEN 0 AND 50",
            name="ck_candidate_profiles_max_experience",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("label"),
    )
    op.create_table(
        "job_matches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("job_posting_id", sa.Uuid(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("recommendation", sa.String(length=30), nullable=False),
        sa.Column(
            "review_status",
            sa.String(length=30),
            server_default="new",
            nullable=False,
        ),
        sa.Column(
            "matched_terms",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "risk_flags",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("matcher_version", sa.String(length=50), nullable=False),
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
            name="ck_job_matches_score",
        ),
        sa.CheckConstraint(
            "recommendation IN "
            "('strong_apply', 'apply', 'review', 'skip')",
            name="ck_job_matches_recommendation",
        ),
        sa.CheckConstraint(
            "review_status IN "
            "('new', 'shortlisted', 'dismissed', 'applied')",
            name="ck_job_matches_review_status",
        ),
        sa.CheckConstraint(
            "char_length(job_content_hash) = 64",
            name="ck_job_matches_job_hash_length",
        ),
        sa.CheckConstraint(
            "char_length(profile_hash) = 64",
            name="ck_job_matches_profile_hash_length",
        ),
        sa.CheckConstraint(
            "char_length(btrim(reason)) > 0",
            name="ck_job_matches_reason_not_blank",
        ),
        sa.CheckConstraint(
            "char_length(btrim(matcher_version)) > 0",
            name="ck_job_matches_version_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["candidate_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_posting_id"],
            ["job_postings.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "job_posting_id",
            name="uq_job_matches_profile_posting",
        ),
    )
    op.create_index(
        "ix_job_matches_profile_recommendation_score",
        "job_matches",
        ["profile_id", "recommendation", "score"],
        unique=False,
    )
    op.create_index(
        "ix_job_matches_review_status_score",
        "job_matches",
        ["review_status", "score"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_job_matches_review_status_score",
        table_name="job_matches",
    )
    op.drop_index(
        "ix_job_matches_profile_recommendation_score",
        table_name="job_matches",
    )
    op.drop_table("job_matches")
    op.drop_table("candidate_profiles")
