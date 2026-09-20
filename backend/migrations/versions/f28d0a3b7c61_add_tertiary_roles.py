"""add tertiary candidate roles

Revision ID: f28d0a3b7c61
Revises: a41f7b2c9d10
Create Date: 2026-09-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f28d0a3b7c61"
down_revision: Union[str, Sequence[str], None] = "a41f7b2c9d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "candidate_profiles",
        sa.Column(
            "tertiary_roles",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_candidate_profiles_tertiary_roles_array",
        "candidate_profiles",
        "jsonb_typeof(tertiary_roles) = 'array'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_candidate_profiles_tertiary_roles_array",
        "candidate_profiles",
        type_="check",
    )
    op.drop_column("candidate_profiles", "tertiary_roles")
