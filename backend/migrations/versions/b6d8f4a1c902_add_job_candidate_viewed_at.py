"""add operator viewed time to job candidates

Revision ID: b6d8f4a1c902
Revises: f3a9c7e1b420
Create Date: 2026-09-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b6d8f4a1c902"
down_revision: Union[str, Sequence[str], None] = "f3a9c7e1b420"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "job_board_candidates",
        sa.Column("operator_viewed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_board_candidates", "operator_viewed_at")
