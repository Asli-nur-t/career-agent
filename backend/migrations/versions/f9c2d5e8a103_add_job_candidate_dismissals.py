"""add job candidate dismissals

Revision ID: f9c2d5e8a103
Revises: e8a1c4d7b920
Create Date: 2026-09-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f9c2d5e8a103"
down_revision: Union[str, Sequence[str], None] = "e8a1c4d7b920"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "job_candidate_dismissals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["job_board_candidates.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["candidate_profiles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "candidate_id",
            name="uq_job_candidate_dismissals_profile_candidate",
        ),
    )
    op.create_index(
        "ix_job_candidate_dismissals_profile_created",
        "job_candidate_dismissals",
        ["profile_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_job_candidate_dismissals_profile_created",
        table_name="job_candidate_dismissals",
    )
    op.drop_table("job_candidate_dismissals")
