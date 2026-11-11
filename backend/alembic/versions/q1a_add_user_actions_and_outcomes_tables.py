"""add user_actions and outcomes tables for duplicate detection and learning

Revision ID: q1a
Revises: 70775023654b
Create Date: 2026-10-20 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "q1a"
down_revision: Union[str, Sequence[str], None] = "70775023654b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    inspector = inspect(conn)
    
    # user_actions table - for duplicate detection and audit trails
    if not inspector.has_table("user_actions"):
        op.create_table(
            "user_actions",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("user_id", sa.String(length=32), nullable=False),
            sa.Column("tool_name", sa.String(length=128), nullable=False),
            sa.Column("args", sa.JSON(), nullable=False),
            sa.Column("fingerprint", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="executing"),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("result_summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("result_data", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
            sa.Column("error_type", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("duration_ms", sa.String(length=20), nullable=False, server_default="0"),
            sa.Column("retry_count", sa.String(length=10), nullable=False, server_default="0"),
            sa.Column("context", sa.String(length=128), nullable=False, server_default="user_request"),
            sa.Column("parent_action_id", sa.String(length=32), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    # Batch operations execute at context exit, so a try/except around
    # ``batch_op.create_index`` cannot make duplicate indexes idempotent.
    # Inspect first and issue only the missing DDL.
    existing = {idx["name"] for idx in inspect(conn).get_indexes("user_actions")}
    for idx_name, columns, unique in (
        ("ix_user_actions_user_id_fingerprint", ["user_id", "fingerprint"], False),
        ("ix_user_actions_started_at", ["started_at"], False),
    ):
        if idx_name not in existing:
            op.create_index(idx_name, "user_actions", columns, unique=unique)
    
    # outcomes table - for learning from task results
    if not inspector.has_table("outcomes"):
        op.create_table(
            "outcomes",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("task_id", sa.String(length=32), nullable=False),
            sa.Column("user_id", sa.String(length=32), nullable=False),
            sa.Column("tool_name", sa.String(length=128), nullable=False),
            sa.Column("success", sa.Boolean(), nullable=False),
            sa.Column("quality_score", sa.Float(), nullable=False, server_default="0.5"),
            sa.Column("result_confidence", sa.Float(), nullable=False, server_default="0.5"),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("duration_seconds", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("input_description", sa.Text(), nullable=False, server_default=""),
            sa.Column("input_args_summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("output_summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("output_tokens_used", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("quality_metric", sa.Float(), nullable=False, server_default="0.5"),
            sa.Column("relevance_score", sa.Float(), nullable=False, server_default="0.5"),
            sa.Column("user_rating", sa.Integer(), nullable=True),
            sa.Column("user_feedback_text", sa.Text(), nullable=False, server_default=""),
            sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
            sa.Column("error_type", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("preference_updates", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    existing = {idx["name"] for idx in inspect(conn).get_indexes("outcomes")}
    for idx_name, columns, unique in (
        ("ix_outcomes_user_id", ["user_id"], False),
        ("ix_outcomes_task_id", ["task_id"], False),
        ("ix_outcomes_tool_name", ["tool_name"], False),
        ("ix_outcomes_created_at", ["created_at"], False),
    ):
        if idx_name not in existing:
            op.create_index(idx_name, "outcomes", columns, unique=unique)


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    inspector = inspect(conn)
    if inspector.has_table("outcomes"):
        for idx_name in (
            "ix_outcomes_created_at", "ix_outcomes_tool_name",
            "ix_outcomes_task_id", "ix_outcomes_user_id",
        ):
            if idx_name in {idx["name"] for idx in inspect(conn).get_indexes("outcomes")}:
                op.drop_index(idx_name, table_name="outcomes")
        op.drop_table("outcomes")

    if inspector.has_table("user_actions"):
        for idx_name in ("ix_user_actions_started_at", "ix_user_actions_user_id_fingerprint"):
            if idx_name in {idx["name"] for idx in inspect(conn).get_indexes("user_actions")}:
                op.drop_index(idx_name, table_name="user_actions")
        op.drop_table("user_actions")
