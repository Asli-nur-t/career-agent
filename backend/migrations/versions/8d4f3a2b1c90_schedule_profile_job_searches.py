"""schedule profile job searches

Revision ID: 8d4f3a2b1c90
Revises: f28d0a3b7c61
Create Date: 2026-09-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8d4f3a2b1c90"
down_revision: Union[str, Sequence[str], None] = "f28d0a3b7c61"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "candidate_profiles",
        sa.Column(
            "job_search_last_checked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "candidate_profiles",
        sa.Column(
            "job_search_next_check_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "candidate_profiles",
        sa.Column("job_search_last_outcome", sa.String(30), nullable=True),
    )
    op.add_column(
        "candidate_profiles",
        sa.Column(
            "job_search_last_error_code",
            sa.String(80),
            nullable=True,
        ),
    )
    op.add_column(
        "candidate_profiles",
        sa.Column(
            "job_search_consecutive_failures",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "candidate_profiles",
        sa.Column(
            "job_search_candidate_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "candidate_profiles",
        sa.Column("job_search_profile_hash", sa.String(64), nullable=True),
    )
    op.create_check_constraint(
        "ck_candidate_profiles_job_search_outcome",
        "candidate_profiles",
        "job_search_last_outcome IS NULL OR "
        "job_search_last_outcome IN "
        "('candidates_found', 'no_results', 'error')",
    )
    op.create_check_constraint(
        "ck_candidate_profiles_job_search_failure_count",
        "candidate_profiles",
        "job_search_consecutive_failures BETWEEN 0 AND 1000",
    )
    op.create_check_constraint(
        "ck_candidate_profiles_job_search_candidate_count",
        "candidate_profiles",
        "job_search_candidate_count BETWEEN 0 AND 1000",
    )
    op.create_check_constraint(
        "ck_candidate_profiles_job_search_hash_length",
        "candidate_profiles",
        "job_search_profile_hash IS NULL OR "
        "char_length(job_search_profile_hash) = 64",
    )
    op.create_index(
        "ix_candidate_profiles_job_search_due",
        "candidate_profiles",
        ["job_search_next_check_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_candidate_profiles_job_search_due",
        table_name="candidate_profiles",
    )
    op.drop_constraint(
        "ck_candidate_profiles_job_search_hash_length",
        "candidate_profiles",
        type_="check",
    )
    op.drop_constraint(
        "ck_candidate_profiles_job_search_candidate_count",
        "candidate_profiles",
        type_="check",
    )
    op.drop_constraint(
        "ck_candidate_profiles_job_search_failure_count",
        "candidate_profiles",
        type_="check",
    )
    op.drop_constraint(
        "ck_candidate_profiles_job_search_outcome",
        "candidate_profiles",
        type_="check",
    )
    op.drop_column("candidate_profiles", "job_search_profile_hash")
    op.drop_column("candidate_profiles", "job_search_candidate_count")
    op.drop_column("candidate_profiles", "job_search_consecutive_failures")
    op.drop_column("candidate_profiles", "job_search_last_error_code")
    op.drop_column("candidate_profiles", "job_search_last_outcome")
    op.drop_column("candidate_profiles", "job_search_next_check_at")
    op.drop_column("candidate_profiles", "job_search_last_checked_at")
