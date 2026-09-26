"""add job application tracking

Revision ID: c7e9a2d4f611
Revises: b6d8f4a1c902
Create Date: 2026-09-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7e9a2d4f611"
down_revision: Union[str, Sequence[str], None] = "b6d8f4a1c902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


APPLICATION_STATUSES = (
    "'to_apply', 'applied', 'interview', 'rejected', 'offer', 'withdrawn'"
)


def upgrade() -> None:
    op.create_table(
        "job_applications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default="to_apply",
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"status IN ({APPLICATION_STATUSES})",
            name="ck_job_applications_status",
        ),
        sa.CheckConstraint(
            "notes IS NULL OR char_length(notes) <= 2000",
            name="ck_job_applications_notes_length",
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
            "candidate_id",
            "profile_id",
            name="uq_job_applications_candidate_profile",
        ),
    )
    op.create_index(
        "ix_job_applications_profile_status_updated",
        "job_applications",
        ["profile_id", "status", "updated_at"],
    )
    op.create_table(
        "job_application_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("previous_status", sa.String(length=30), nullable=True),
        sa.Column("new_status", sa.String(length=30), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "previous_status IS NULL OR "
            f"previous_status IN ({APPLICATION_STATUSES})",
            name="ck_job_application_events_previous_status",
        ),
        sa.CheckConstraint(
            f"new_status IN ({APPLICATION_STATUSES})",
            name="ck_job_application_events_new_status",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["job_applications.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_job_application_events_application_created",
        "job_application_events",
        ["application_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_job_application_events_application_created",
        table_name="job_application_events",
    )
    op.drop_table("job_application_events")
    op.drop_index(
        "ix_job_applications_profile_status_updated",
        table_name="job_applications",
    )
    op.drop_table("job_applications")
