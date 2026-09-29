"""add operational query indexes

Revision ID: e8b41c2d7a90
Revises: c4a72f18e9d1
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8b41c2d7a90"
down_revision: str | None = "c4a72f18e9d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_jobs_api_key_id_id", "jobs", ["api_key_id", "id"])
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"])
    op.create_index(
        "ix_jobs_terminal_finished_at",
        "jobs",
        ["finished_at", "id"],
        postgresql_where=sa.text(
            "status IN ('succeeded', 'failed', 'dead_lettered', 'cancelled')"
        ),
    )
    op.create_index(
        "ix_job_transitions_to_status_created_at",
        "job_transitions",
        ["to_status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_job_transitions_to_status_created_at", table_name="job_transitions")
    op.drop_index("ix_jobs_terminal_finished_at", table_name="jobs")
    op.drop_index("ix_jobs_created_at", table_name="jobs")
    op.drop_index("ix_jobs_api_key_id_id", table_name="jobs")
