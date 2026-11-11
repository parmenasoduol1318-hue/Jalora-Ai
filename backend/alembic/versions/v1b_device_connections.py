"""Add scoped device companion connections and message approvals."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "v1b_device_connections"
down_revision: Union[str, Sequence[str], None] = "v1a_merge_people_profiles_calendar_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_connections",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("user_id", sa.String(length=32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("platform", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_device_connections_user_id", "device_connections", ["user_id"])
    op.create_index("ix_device_connections_status", "device_connections", ["status"])
    op.create_table(
        "device_messages",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "device_id", sa.String(length=32),
            sa.ForeignKey("device_connections.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("user_id", sa.String(length=32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("source_app", sa.String(length=120), nullable=False),
        sa.Column("sender", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("draft_reply", sa.Text(), nullable=False, server_default=""),
        sa.Column("queued_reply", sa.Text(), nullable=True),
        sa.Column("action_status", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("device_id", "external_id", name="uq_device_message_external_id"),
    )
    op.create_index("ix_device_messages_device_id", "device_messages", ["device_id"])
    op.create_index("ix_device_messages_user_id", "device_messages", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_device_messages_user_id", table_name="device_messages")
    op.drop_index("ix_device_messages_device_id", table_name="device_messages")
    op.drop_table("device_messages")
    op.drop_index("ix_device_connections_status", table_name="device_connections")
    op.drop_index("ix_device_connections_user_id", table_name="device_connections")
    op.drop_table("device_connections")
