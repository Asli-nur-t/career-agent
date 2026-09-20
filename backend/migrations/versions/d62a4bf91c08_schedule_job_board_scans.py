"""schedule job board scans

Revision ID: d62a4bf91c08
Revises: c51d8a743e20
Create Date: 2026-09-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d62a4bf91c08"
down_revision: Union[str, Sequence[str], None] = "c51d8a743e20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "company_web_profiles",
        sa.Column(
            "job_boards_last_checked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "company_web_profiles",
        sa.Column(
            "job_boards_next_check_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "company_web_profiles",
        sa.Column("job_boards_last_outcome", sa.String(30), nullable=True),
    )
    op.add_column(
        "company_web_profiles",
        sa.Column(
            "job_boards_last_error_code",
            sa.String(80),
            nullable=True,
        ),
    )
    op.add_column(
        "company_web_profiles",
        sa.Column(
            "job_boards_consecutive_failures",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "company_web_profiles",
        sa.Column(
            "job_boards_candidate_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_company_web_profiles_job_board_scan_outcome",
        "company_web_profiles",
        "job_boards_last_outcome IS NULL OR "
        "job_boards_last_outcome IN "
        "('candidates_found', 'no_results', 'error')",
    )
    op.create_check_constraint(
        "ck_company_web_profiles_job_board_failure_count",
        "company_web_profiles",
        "job_boards_consecutive_failures BETWEEN 0 AND 1000",
    )
    op.create_check_constraint(
        "ck_company_web_profiles_job_board_candidate_count",
        "company_web_profiles",
        "job_boards_candidate_count BETWEEN 0 AND 100",
    )
    op.create_index(
        "ix_company_web_profiles_job_board_scan_due",
        "company_web_profiles",
        ["status", "job_boards_next_check_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_company_web_profiles_job_board_scan_due",
        table_name="company_web_profiles",
    )
    op.drop_constraint(
        "ck_company_web_profiles_job_board_candidate_count",
        "company_web_profiles",
        type_="check",
    )
    op.drop_constraint(
        "ck_company_web_profiles_job_board_failure_count",
        "company_web_profiles",
        type_="check",
    )
    op.drop_constraint(
        "ck_company_web_profiles_job_board_scan_outcome",
        "company_web_profiles",
        type_="check",
    )
    op.drop_column("company_web_profiles", "job_boards_candidate_count")
    op.drop_column(
        "company_web_profiles", "job_boards_consecutive_failures"
    )
    op.drop_column("company_web_profiles", "job_boards_last_error_code")
    op.drop_column("company_web_profiles", "job_boards_last_outcome")
    op.drop_column("company_web_profiles", "job_boards_next_check_at")
    op.drop_column("company_web_profiles", "job_boards_last_checked_at")
