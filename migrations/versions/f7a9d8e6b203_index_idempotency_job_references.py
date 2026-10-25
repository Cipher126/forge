"""index idempotency job references

Revision ID: f7a9d8e6b203
Revises: f2c6d7e8a901
Create Date: 2026-10-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f7a9d8e6b203"
down_revision: str | None = "f2c6d7e8a901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_idempotency_keys_job_id", "idempotency_keys", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_keys_job_id", table_name="idempotency_keys")
