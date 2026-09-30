"""add admission slots

Revision ID: f2c6d7e8a901
Revises: e8b41c2d7a90
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2c6d7e8a901"
down_revision: str | None = "e8b41c2d7a90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admission_slots",
        sa.Column("slot_number", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            ondelete="SET NULL",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("slot_number"),
        sa.UniqueConstraint("job_id"),
    )
    op.execute("INSERT INTO admission_slots (slot_number) SELECT generate_series(1, 50000)")
    op.execute(
        """
        WITH outstanding AS (
            SELECT id, row_number() OVER (ORDER BY created_at, id) AS slot_number
            FROM jobs
            WHERE status IN (
                'scheduled', 'queued', 'running', 'retry_scheduled',
                'cancellation_requested'
            )
        )
        UPDATE admission_slots AS slots
        SET job_id = outstanding.id
        FROM outstanding
        WHERE slots.slot_number = outstanding.slot_number
        """
    )


def downgrade() -> None:
    op.drop_table("admission_slots")
