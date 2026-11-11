"""Add target-approved device pairing and bounded remote commands."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "v1d_remote_device_companion"
down_revision: Union[str, Sequence[str], None] = "v1c_local_device_apps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_pairing_requests",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("user_id", sa.String(length=32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("pairing_code_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("platform", sa.String(length=24), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "approved_device_id",
            sa.String(length=32),
            sa.ForeignKey("device_connections.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_device_pairing_requests_user_id",
        "device_pairing_requests",
        ["user_id"],
    )
    op.create_index(
        "ix_device_pairing_requests_pairing_code_hash",
        "device_pairing_requests",
        ["pairing_code_hash"],
    )
    op.create_index(
        "ix_device_pairing_requests_status",
        "device_pairing_requests",
        ["status"],
    )

    op.create_table(
        "device_remote_commands",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("user_id", sa.String(length=32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "device_id",
            sa.String(length=32),
            sa.ForeignKey("device_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_device_remote_commands_user_id", "device_remote_commands", ["user_id"]
    )
    op.create_index(
        "ix_device_remote_commands_device_id", "device_remote_commands", ["device_id"]
    )
    op.create_index(
        "ix_device_remote_commands_status", "device_remote_commands", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_device_remote_commands_status", table_name="device_remote_commands")
    op.drop_index("ix_device_remote_commands_device_id", table_name="device_remote_commands")
    op.drop_index("ix_device_remote_commands_user_id", table_name="device_remote_commands")
    op.drop_table("device_remote_commands")
    op.drop_index("ix_device_pairing_requests_status", table_name="device_pairing_requests")
    op.drop_index(
        "ix_device_pairing_requests_pairing_code_hash",
        table_name="device_pairing_requests",
    )
    op.drop_index(
        "ix_device_pairing_requests_user_id", table_name="device_pairing_requests"
    )
    op.drop_table("device_pairing_requests")
