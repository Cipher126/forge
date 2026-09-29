"""add audit events

Revision ID: c4a72f18e9d1
Revises: 9d2f8a1c6b40
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4a72f18e9d1"
down_revision: str | None = "9d2f8a1c6b40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("actor_api_key_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_public_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(actor_public_id) BETWEEN 1 AND 64",
            name="ck_audit_events_actor_public_id_length",
        ),
        sa.CheckConstraint(
            "char_length(action) BETWEEN 1 AND 128",
            name="ck_audit_events_action_length",
        ),
        sa.CheckConstraint(
            "char_length(resource_type) BETWEEN 1 AND 64",
            name="ck_audit_events_resource_type_length",
        ),
        sa.CheckConstraint(
            "char_length(resource_id) BETWEEN 1 AND 128",
            name="ck_audit_events_resource_id_length",
        ),
        sa.ForeignKeyConstraint(
            ["actor_api_key_id"],
            ["api_keys.id"],
            name="fk_audit_events_actor_api_key_id_api_keys",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
    )
    op.create_index("ix_audit_events_action_id", "audit_events", ["action", "id"])
    op.create_index(
        "ix_audit_events_resource_id",
        "audit_events",
        ["resource_type", "resource_id", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_resource_id", table_name="audit_events")
    op.drop_index("ix_audit_events_action_id", table_name="audit_events")
    op.drop_table("audit_events")
