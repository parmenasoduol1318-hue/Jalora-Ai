"""add feedback and workflow_patterns tables (Phase K §27, §70, §71, §72)

Revision ID: k1f2e3d4c5b6
Revises: e5b7c9d1f3a5
Create Date: 2026-10-09 11:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "k1f2e3d4c5b6"
down_revision = "e5b7c9d1f3a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feedback",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("user_id", sa.String(length=32), nullable=False, index=True),
        sa.Column("message_id", sa.String(length=32), nullable=True, index=True),
        sa.Column("task_id", sa.String(length=32), nullable=True, index=True),
        sa.Column("run_id", sa.String(length=32), nullable=True, index=True),
        sa.Column("tool_name", sa.String(length=128), nullable=True),
        sa.Column("sentiment", sa.String(length=16), nullable=False, server_default="positive"),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    op.create_table(
        "workflow_patterns",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("user_id", sa.String(length=32), nullable=False, index=True),
        sa.Column("pattern_type", sa.String(length=16), nullable=False, server_default="success", index=True),
        sa.Column("label", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("fingerprint", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("context", sa.Text(), nullable=False, server_default=""),
        sa.Column("failing_tool", sa.String(length=128), nullable=True),
        sa.Column("cause", sa.Text(), nullable=False, server_default=""),
        sa.Column("replacement", sa.Text(), nullable=False, server_default=""),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )


def downgrade() -> None:
    op.drop_table("workflow_patterns")
    op.drop_table("feedback")
