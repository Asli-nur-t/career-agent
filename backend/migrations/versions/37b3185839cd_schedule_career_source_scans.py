"""schedule career source scans

Revision ID: 37b3185839cd
Revises: 72c40d9e9f81
Create Date: 2026-09-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "37b3185839cd"
down_revision: Union[str, Sequence[str], None] = "72c40d9e9f81"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "company_web_profiles",
        sa.Column(
            "career_sources_last_checked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "company_web_profiles",
        sa.Column(
            "career_sources_next_check_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "company_web_profiles",
        sa.Column(
            "career_sources_last_outcome",
            sa.String(length=30),
            nullable=True,
        ),
    )
    op.add_column(
        "company_web_profiles",
        sa.Column(
            "career_sources_last_error_code",
            sa.String(length=80),
            nullable=True,
        ),
    )
    op.add_column(
        "company_web_profiles",
        sa.Column(
            "career_sources_consecutive_failures",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "company_web_profiles",
        sa.Column(
            "career_sources_candidate_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_company_web_profiles_career_scan_outcome",
        "company_web_profiles",
        "career_sources_last_outcome IS NULL OR "
        "career_sources_last_outcome IN "
        "('candidates_found', 'no_results', 'error')",
    )
    op.create_check_constraint(
        "ck_company_web_profiles_career_failure_count",
        "company_web_profiles",
        "career_sources_consecutive_failures BETWEEN 0 AND 1000",
    )
    op.create_check_constraint(
        "ck_company_web_profiles_career_candidate_count",
        "company_web_profiles",
        "career_sources_candidate_count BETWEEN 0 AND 100",
    )
    op.create_index(
        "ix_company_web_profiles_career_scan_due",
        "company_web_profiles",
        ["status", "career_sources_next_check_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_company_web_profiles_career_scan_due",
        table_name="company_web_profiles",
    )
    op.drop_constraint(
        "ck_company_web_profiles_career_candidate_count",
        "company_web_profiles",
        type_="check",
    )
    op.drop_constraint(
        "ck_company_web_profiles_career_failure_count",
        "company_web_profiles",
        type_="check",
    )
    op.drop_constraint(
        "ck_company_web_profiles_career_scan_outcome",
        "company_web_profiles",
        type_="check",
    )
    op.drop_column(
        "company_web_profiles", "career_sources_candidate_count"
    )
    op.drop_column(
        "company_web_profiles", "career_sources_consecutive_failures"
    )
    op.drop_column(
        "company_web_profiles", "career_sources_last_error_code"
    )
    op.drop_column(
        "company_web_profiles", "career_sources_last_outcome"
    )
    op.drop_column(
        "company_web_profiles", "career_sources_next_check_at"
    )
    op.drop_column(
        "company_web_profiles", "career_sources_last_checked_at"
    )
