"""add remote location preferences

Revision ID: c6a42e91d5b7
Revises: 8d4f3a2b1c90
Create Date: 2026-09-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c6a42e91d5b7"
down_revision: Union[str, Sequence[str], None] = "8d4f3a2b1c90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "candidate_profiles",
        sa.Column(
            "preferred_remote_locations",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_candidate_profiles_remote_locations_array",
        "candidate_profiles",
        "jsonb_typeof(preferred_remote_locations) = 'array'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_candidate_profiles_remote_locations_array",
        "candidate_profiles",
        type_="check",
    )
    op.drop_column("candidate_profiles", "preferred_remote_locations")
