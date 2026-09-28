"""expand manual job providers

Revision ID: d4f8b2a7c913
Revises: c7e9a2d4f611
Create Date: 2026-09-26
"""

from typing import Sequence, Union

from alembic import op


revision: str = "d4f8b2a7c913"
down_revision: Union[str, Sequence[str], None] = "c7e9a2d4f611"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_PROVIDERS = (
    "'linkedin', 'kariyer', 'indeed', 'glassdoor', "
    "'greenhouse', 'lever', 'ashby'"
)
NEW_PROVIDERS = (
    OLD_PROVIDERS
    + ", 'techcareer', 'yenibiris', 'secretcv', 'toptalent', "
    + "'weworkremotely', 'remoteok', 'remotive', 'jobicy'"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_job_board_candidates_provider",
        "job_board_candidates",
        type_="check",
    )
    op.create_check_constraint(
        "ck_job_board_candidates_provider",
        "job_board_candidates",
        f"provider IN ({NEW_PROVIDERS})",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_job_board_candidates_provider",
        "job_board_candidates",
        type_="check",
    )
    op.create_check_constraint(
        "ck_job_board_candidates_provider",
        "job_board_candidates",
        f"provider IN ({OLD_PROVIDERS})",
    )
