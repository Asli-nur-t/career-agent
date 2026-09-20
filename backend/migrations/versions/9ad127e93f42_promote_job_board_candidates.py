"""promote job board candidates

Revision ID: 9ad127e93f42
Revises: 64f3a9c20b11
Create Date: 2026-09-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9ad127e93f42"
down_revision: Union[str, Sequence[str], None] = "64f3a9c20b11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "job_postings",
        sa.Column("job_board_candidate_id", sa.Uuid(), nullable=True),
    )
    op.alter_column(
        "job_postings",
        "career_source_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.create_foreign_key(
        "fk_job_postings_job_board_candidate",
        "job_postings",
        "job_board_candidates",
        ["job_board_candidate_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_job_postings_job_board_candidate",
        "job_postings",
        ["job_board_candidate_id"],
    )
    op.create_check_constraint(
        "ck_job_postings_exactly_one_source",
        "job_postings",
        "(career_source_id IS NOT NULL AND "
        "job_board_candidate_id IS NULL) OR "
        "(career_source_id IS NULL AND "
        "job_board_candidate_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_job_postings_exactly_one_source",
        "job_postings",
        type_="check",
    )
    op.execute(
        "DELETE FROM job_postings WHERE career_source_id IS NULL"
    )
    op.drop_constraint(
        "uq_job_postings_job_board_candidate",
        "job_postings",
        type_="unique",
    )
    op.drop_constraint(
        "fk_job_postings_job_board_candidate",
        "job_postings",
        type_="foreignkey",
    )
    op.alter_column(
        "job_postings",
        "career_source_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.drop_column("job_postings", "job_board_candidate_id")
