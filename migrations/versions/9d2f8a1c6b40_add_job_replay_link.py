"""add job replay link

Revision ID: 9d2f8a1c6b40
Revises: 477abf1deae6
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9d2f8a1c6b40"
down_revision: str | None = "477abf1deae6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("replayed_from_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_jobs_replayed_from_job_id_jobs",
        "jobs",
        "jobs",
        ["replayed_from_job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_jobs_replayed_from_job_id",
        "jobs",
        ["replayed_from_job_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_replayed_from_job_id", table_name="jobs")
    op.drop_constraint("fk_jobs_replayed_from_job_id_jobs", "jobs", type_="foreignkey")
    op.drop_column("jobs", "replayed_from_job_id")
