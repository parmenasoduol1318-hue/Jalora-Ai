"""reconcile schema with model metadata: memories.meta, documents.progress,
missing FKs / indexes on feedback, refresh_tokens, workflow_patterns, and NOT
NULL integrations.scopes/config.

Revision ID: m1a_schema_reconcile
Revises: l2a_phase_l_hardening
Create Date: 2026-10-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "m1a_schema_reconcile"
down_revision = "l2a_phase_l_hardening"
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _cols(table: str) -> set[str]:
    return {c["name"] for c in _inspector().get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in _inspector().get_indexes(table)}


def _fks(table: str) -> set[str]:
    return {fk.get("name") for fk in _inspector().get_foreign_keys(table) if fk.get("name")}


def upgrade() -> None:
    # -- columns the models declare but no migration ever added ----------------
    if "meta" not in _cols("memories"):
        op.add_column("memories", sa.Column("meta", sa.JSON(), nullable=True))

    if "progress" not in _cols("documents"):
        op.add_column(
            "documents",
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        )

    # -- foreign keys the models declare but earlier create_table calls omitted -
    if "fk_feedback_user_id_users" not in _fks("feedback"):
        with op.batch_alter_table("feedback") as batch_op:
            batch_op.create_foreign_key(
                "fk_feedback_user_id_users", "users", ["user_id"], ["id"]
            )
            batch_op.create_foreign_key(
                "fk_feedback_task_id_tasks", "tasks", ["task_id"], ["id"]
            )

    if "fk_refresh_tokens_user_id_users" not in _fks("refresh_tokens"):
        with op.batch_alter_table("refresh_tokens") as batch_op:
            batch_op.create_foreign_key(
                "fk_refresh_tokens_user_id_users", "users", ["user_id"], ["id"]
            )

    if "fk_workflow_patterns_user_id_users" not in _fks("workflow_patterns"):
        with op.batch_alter_table("workflow_patterns") as batch_op:
            batch_op.create_foreign_key(
                "fk_workflow_patterns_user_id_users", "users", ["user_id"], ["id"]
            )

    # -- indexes the models declare (created_at lookups) -----------------------
    if "ix_feedback_created_at" not in _indexes("feedback"):
        op.create_index("ix_feedback_created_at", "feedback", ["created_at"])

    if "ix_workflow_patterns_created_at" not in _indexes("workflow_patterns"):
        op.create_index(
            "ix_workflow_patterns_created_at", "workflow_patterns", ["created_at"]
        )

    # -- integrations.scopes/config are non-nullable in the model --------------
    op.execute("UPDATE integrations SET scopes = '[]' WHERE scopes IS NULL")
    op.execute("UPDATE integrations SET config = '{}' WHERE config IS NULL")
    with op.batch_alter_table("integrations") as batch_op:
        batch_op.alter_column("scopes", existing_type=sa.JSON(), nullable=False)
        batch_op.alter_column("config", existing_type=sa.JSON(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("integrations") as batch_op:
        batch_op.alter_column("scopes", existing_type=sa.JSON(), nullable=True)
        batch_op.alter_column("config", existing_type=sa.JSON(), nullable=True)

    op.drop_index("ix_workflow_patterns_created_at", table_name="workflow_patterns")
    op.drop_index("ix_feedback_created_at", table_name="feedback")

    with op.batch_alter_table("workflow_patterns") as batch_op:
        batch_op.drop_constraint("fk_workflow_patterns_user_id_users", type_="foreignkey")

    with op.batch_alter_table("refresh_tokens") as batch_op:
        batch_op.drop_constraint("fk_refresh_tokens_user_id_users", type_="foreignkey")

    with op.batch_alter_table("feedback") as batch_op:
        batch_op.drop_constraint("fk_feedback_task_id_tasks", type_="foreignkey")
        batch_op.drop_constraint("fk_feedback_user_id_users", type_="foreignkey")

    op.drop_column("documents", "progress")
    op.drop_column("memories", "meta")
