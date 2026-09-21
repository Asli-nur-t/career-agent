"""add official ATS job candidates

Revision ID: d91f4a6b2c73
Revises: c6a42e91d5b7
Create Date: 2026-09-21
"""

from typing import Sequence, Union

from alembic import op


revision: str = "d91f4a6b2c73"
down_revision: Union[str, Sequence[str], None] = "c6a42e91d5b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_job_board_candidates_provider",
        "job_board_candidates",
        type_="check",
    )
    op.create_check_constraint(
        "ck_job_board_candidates_provider",
        "job_board_candidates",
        "provider IN "
        "('linkedin', 'kariyer', 'indeed', 'glassdoor', "
        "'greenhouse', 'lever', 'ashby')",
    )


def downgrade() -> None:
    bind = op.get_bind()
    ats_candidate_count = bind.exec_driver_sql(
        """
        SELECT count(*)
        FROM job_board_candidates
        WHERE provider IN ('greenhouse', 'lever', 'ashby')
        """
    ).scalar_one()
    if ats_candidate_count:
        raise RuntimeError(
            "Official ATS candidates prevent a non-destructive downgrade."
        )
    op.drop_constraint(
        "ck_job_board_candidates_provider",
        "job_board_candidates",
        type_="check",
    )
    op.create_check_constraint(
        "ck_job_board_candidates_provider",
        "job_board_candidates",
        "provider IN ('linkedin', 'kariyer', 'indeed', 'glassdoor')",
    )
