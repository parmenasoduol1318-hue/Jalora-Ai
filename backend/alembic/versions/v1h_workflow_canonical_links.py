"""Link saved workflow execution history to canonical task lifecycle rows."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "v1h_workflow_canonical_links"
down_revision: Union[str, Sequence[str], None] = "v1g_canonical_execution_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    execution_columns = {
        column["name"]
        for column in inspector.get_columns("workflow_execution_records")
    }
    if "task_id" not in execution_columns:
        op.add_column(
            "workflow_execution_records",
            sa.Column("task_id", sa.String(32), nullable=True),
        )
    if "run_id" not in execution_columns:
        op.add_column(
            "workflow_execution_records",
            sa.Column("run_id", sa.String(32), nullable=True),
        )
    result_columns = {
        column["name"]
        for column in inspector.get_columns("workflow_step_results")
    }
    if "task_step_id" not in result_columns:
        op.add_column(
            "workflow_step_results",
            sa.Column("task_step_id", sa.String(32), nullable=True),
        )

    indexes = {
        (table, index["name"])
        for table in ("workflow_execution_records", "workflow_step_results")
        for index in inspector.get_indexes(table)
    }
    for table, index_name, column in (
        (
            "workflow_execution_records",
            "ix_workflow_execution_records_task_id",
            "task_id",
        ),
        (
            "workflow_execution_records",
            "ix_workflow_execution_records_run_id",
            "run_id",
        ),
        (
            "workflow_step_results",
            "ix_workflow_step_results_task_step_id",
            "task_step_id",
        ),
    ):
        if (table, index_name) not in indexes:
            op.create_index(index_name, table, [column])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table, index_name in (
        ("workflow_step_results", "ix_workflow_step_results_task_step_id"),
        ("workflow_execution_records", "ix_workflow_execution_records_run_id"),
        ("workflow_execution_records", "ix_workflow_execution_records_task_id"),
    ):
        if index_name in {item["name"] for item in inspector.get_indexes(table)}:
            op.drop_index(index_name, table_name=table)
    with op.batch_alter_table("workflow_step_results") as batch:
        if "task_step_id" in {
            column["name"]
            for column in sa.inspect(bind).get_columns("workflow_step_results")
        }:
            batch.drop_column("task_step_id")
    execution_columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("workflow_execution_records")
    }
    with op.batch_alter_table("workflow_execution_records") as batch:
        if "run_id" in execution_columns:
            batch.drop_column("run_id")
        if "task_id" in execution_columns:
            batch.drop_column("task_id")
