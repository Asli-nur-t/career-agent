"""filter job board identities

Revision ID: e73c8d12a4f9
Revises: d62a4bf91c08
Create Date: 2026-09-20
"""

from typing import Sequence, Union

from alembic import op


revision: str = "e73c8d12a4f9"
down_revision: Union[str, Sequence[str], None] = "d62a4bf91c08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_job_board_candidates_status",
        "job_board_candidates",
        type_="check",
    )
    op.create_check_constraint(
        "ck_job_board_candidates_status",
        "job_board_candidates",
        "status IN "
        "('needs_review', 'approved', 'rejected', 'filtered_out')",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE job_board_candidates "
        "SET status = 'rejected' "
        "WHERE status = 'filtered_out'"
    )
    op.drop_constraint(
        "ck_job_board_candidates_status",
        "job_board_candidates",
        type_="check",
    )
    op.create_check_constraint(
        "ck_job_board_candidates_status",
        "job_board_candidates",
        "status IN ('needs_review', 'approved', 'rejected')",
    )
