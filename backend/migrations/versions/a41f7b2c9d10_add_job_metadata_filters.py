"""add job metadata filters

Revision ID: a41f7b2c9d10
Revises: e73c8d12a4f9
Create Date: 2026-09-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a41f7b2c9d10"
down_revision: Union[str, Sequence[str], None] = "e73c8d12a4f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "candidate_profiles",
        sa.Column(
            "excluded_locations",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "candidate_profiles",
        sa.Column(
            "allowed_work_modes",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "candidate_profiles",
        sa.Column(
            "location_filter_mode",
            sa.String(length=20),
            server_default="prefer",
            nullable=False,
        ),
    )
    op.add_column(
        "candidate_profiles",
        sa.Column(
            "max_listing_age_days",
            sa.Integer(),
            server_default="30",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_candidate_profiles_excluded_locations_array",
        "candidate_profiles",
        "jsonb_typeof(excluded_locations) = 'array'",
    )
    op.create_check_constraint(
        "ck_candidate_profiles_work_modes_array",
        "candidate_profiles",
        "jsonb_typeof(allowed_work_modes) = 'array'",
    )
    op.create_check_constraint(
        "ck_candidate_profiles_location_filter_mode",
        "candidate_profiles",
        "location_filter_mode IN ('prefer', 'require')",
    )
    op.create_check_constraint(
        "ck_candidate_profiles_max_listing_age",
        "candidate_profiles",
        "max_listing_age_days BETWEEN 1 AND 3650",
    )

    op.add_column(
        "job_board_candidates",
        sa.Column(
            "work_mode",
            sa.String(length=20),
            server_default="unknown",
            nullable=False,
        ),
    )
    op.add_column(
        "job_board_candidates",
        sa.Column(
            "employment_type",
            sa.String(length=30),
            server_default="unknown",
            nullable=False,
        ),
    )
    op.add_column(
        "job_board_candidates",
        sa.Column("published_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "job_board_candidates",
        sa.Column(
            "activity_state",
            sa.String(length=20),
            server_default="unknown",
            nullable=False,
        ),
    )
    op.add_column(
        "job_board_candidates",
        sa.Column(
            "activity_code",
            sa.String(length=80),
            server_default="not_checked",
            nullable=False,
        ),
    )
    op.add_column(
        "job_board_candidates",
        sa.Column("activity_checked_at", sa.DateTime(timezone=True)),
    )
    op.create_check_constraint(
        "ck_job_board_candidates_work_mode",
        "job_board_candidates",
        "work_mode IN ('remote', 'hybrid', 'onsite', 'unknown')",
    )
    op.create_check_constraint(
        "ck_job_board_candidates_employment_type",
        "job_board_candidates",
        "employment_type IN "
        "('full_time', 'part_time', 'contract', 'internship', "
        "'temporary', 'unknown')",
    )
    op.create_check_constraint(
        "ck_job_board_candidates_activity_state",
        "job_board_candidates",
        "activity_state IN ('active', 'closed', 'unknown')",
    )
    op.create_check_constraint(
        "ck_job_board_candidates_activity_code_not_blank",
        "job_board_candidates",
        "char_length(btrim(activity_code)) > 0",
    )
    op.create_index(
        "ix_job_board_candidates_status_published",
        "job_board_candidates",
        ["status", "published_at"],
    )

    op.add_column(
        "job_postings",
        sa.Column(
            "work_mode",
            sa.String(length=20),
            server_default="unknown",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_job_postings_work_mode",
        "job_postings",
        "work_mode IN ('remote', 'hybrid', 'onsite', 'unknown')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_job_postings_work_mode",
        "job_postings",
        type_="check",
    )
    op.drop_column("job_postings", "work_mode")

    op.drop_index(
        "ix_job_board_candidates_status_published",
        table_name="job_board_candidates",
    )
    op.drop_constraint(
        "ck_job_board_candidates_activity_code_not_blank",
        "job_board_candidates",
        type_="check",
    )
    op.drop_constraint(
        "ck_job_board_candidates_activity_state",
        "job_board_candidates",
        type_="check",
    )
    op.drop_constraint(
        "ck_job_board_candidates_employment_type",
        "job_board_candidates",
        type_="check",
    )
    op.drop_constraint(
        "ck_job_board_candidates_work_mode",
        "job_board_candidates",
        type_="check",
    )
    op.drop_column("job_board_candidates", "activity_checked_at")
    op.drop_column("job_board_candidates", "activity_code")
    op.drop_column("job_board_candidates", "activity_state")
    op.drop_column("job_board_candidates", "published_at")
    op.drop_column("job_board_candidates", "employment_type")
    op.drop_column("job_board_candidates", "work_mode")

    op.drop_constraint(
        "ck_candidate_profiles_max_listing_age",
        "candidate_profiles",
        type_="check",
    )
    op.drop_constraint(
        "ck_candidate_profiles_location_filter_mode",
        "candidate_profiles",
        type_="check",
    )
    op.drop_constraint(
        "ck_candidate_profiles_work_modes_array",
        "candidate_profiles",
        type_="check",
    )
    op.drop_constraint(
        "ck_candidate_profiles_excluded_locations_array",
        "candidate_profiles",
        type_="check",
    )
    op.drop_column("candidate_profiles", "max_listing_age_days")
    op.drop_column("candidate_profiles", "location_filter_mode")
    op.drop_column("candidate_profiles", "allowed_work_modes")
    op.drop_column("candidate_profiles", "excluded_locations")
