"""Additive canonical task execution lifecycle fields and attempt journals."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "v1g_canonical_execution_lifecycle"
down_revision: Union[str, Sequence[str], None] = "v1f_approval_plan_step"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_step_attempts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("task_id", sa.String(32), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("step_id", sa.String(32), sa.ForeignKey("task_steps.id"), nullable=False),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("tool_call_id", sa.String(128), nullable=True),
        sa.Column("state", sa.String(24), nullable=False, server_default="prepared"),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("provider_idempotency_key", sa.String(128), nullable=True),
        sa.Column("result_data", sa.JSON(), nullable=True),
        sa.Column("outcome_status", sa.String(32), nullable=False, server_default="prepared"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("step_id", "attempt_number", name="uq_task_step_attempt_number"),
    )
    op.create_index("ix_task_step_attempts_task_id", "task_step_attempts", ["task_id"])
    op.create_index("ix_task_step_attempts_step_id", "task_step_attempts", ["step_id"])
    op.create_index("ix_task_step_attempts_run_id", "task_step_attempts", ["run_id"])
    op.create_index("ix_task_step_attempts_state", "task_step_attempts", ["state"])

    op.create_table(
        "approval_revisions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("approval_id", sa.String(32), sa.ForeignKey("approvals.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("editor_user_id", sa.String(32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("approval_id", "version", name="uq_approval_revision_version"),
    )
    op.create_index("ix_approval_revisions_approval_id", "approval_revisions", ["approval_id"])

    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("contract_version", sa.String(8), nullable=False, server_default="0"))
        batch.add_column(sa.Column("input_data", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("target_data", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("trigger_data", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("trigger_key", sa.String(64), nullable=True))
    op.create_index("ix_tasks_trigger_key", "tasks", ["trigger_key"], unique=True)

    with op.batch_alter_table("task_steps") as batch:
        batch.add_column(sa.Column("plan_step_id", sa.String(128), nullable=True))
        batch.add_column(sa.Column("dependency_step_ids", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("capability", sa.String(128), nullable=True))
        batch.add_column(sa.Column("tool_version", sa.String(64), nullable=True))
        batch.add_column(sa.Column("policy_decision", sa.String(16), nullable=True))
        batch.add_column(sa.Column("policy_version", sa.String(32), nullable=True))
        batch.add_column(sa.Column("completion_outcomes", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("action_id", sa.String(32), nullable=True))
        batch.add_column(sa.Column("approval_id", sa.String(32), nullable=True))
        batch.add_column(sa.Column("idempotency_key", sa.String(64), nullable=True))
        batch.add_column(sa.Column("result_data", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("outcome_status", sa.String(32), nullable=False, server_default="planned"))
        batch.add_column(sa.Column("verification_status", sa.String(24), nullable=False, server_default="pending"))
        batch.add_column(sa.Column("error_code", sa.String(64), nullable=True))
        batch.add_column(sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_unique_constraint(
            "uq_task_steps_task_plan_id", ["task_id", "plan_step_id"]
        )
    op.create_index("ix_task_steps_action_id", "task_steps", ["action_id"], unique=True)

    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column(
            "task_id", sa.String(32),
            sa.ForeignKey("tasks.id", name="fk_agent_runs_task_id_tasks"),
            nullable=True,
        ))
        batch.add_column(sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE agent_runs SET started_at = created_at WHERE started_at IS NULL")
    with op.batch_alter_table("agent_runs") as batch:
        batch.alter_column(
            "started_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
    op.create_index("ix_agent_runs_task_id", "agent_runs", ["task_id"])

    with op.batch_alter_table("approvals") as batch:
        batch.add_column(sa.Column(
            "task_id", sa.String(32),
            sa.ForeignKey("tasks.id", name="fk_approvals_task_id_tasks"),
            nullable=True,
        ))
        batch.add_column(sa.Column(
            "task_step_id", sa.String(32),
            sa.ForeignKey("task_steps.id", name="fk_approvals_task_step_id_task_steps"),
            nullable=True,
        ))
        batch.add_column(sa.Column("decision_status", sa.String(16), nullable=False, server_default="pending"))
        batch.add_column(sa.Column("claim_status", sa.String(16), nullable=False, server_default="unclaimed"))
        batch.add_column(sa.Column("payload_version", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("payload_digest", sa.String(64), nullable=True))
        batch.add_column(sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column(
            "decision_actor_id", sa.String(32),
            sa.ForeignKey("users.id", name="fk_approvals_decision_actor_id_users"),
            nullable=True,
        ))
    op.create_index("ix_approvals_task_id", "approvals", ["task_id"])
    op.create_index("ix_approvals_task_step_id", "approvals", ["task_step_id"])

    with op.batch_alter_table("activity_logs") as batch:
        batch.add_column(sa.Column(
            "task_id", sa.String(32),
            sa.ForeignKey("tasks.id", name="fk_activity_logs_task_id_tasks"),
            nullable=True,
        ))
        batch.add_column(sa.Column(
            "task_step_id", sa.String(32),
            sa.ForeignKey("task_steps.id", name="fk_activity_logs_task_step_id_task_steps"),
            nullable=True,
        ))
        batch.add_column(sa.Column("action_id", sa.String(32), nullable=True))
        batch.add_column(sa.Column(
            "attempt_id", sa.String(32),
            sa.ForeignKey("task_step_attempts.id", name="fk_activity_logs_attempt_id_task_step_attempts"),
            nullable=True,
        ))
        batch.add_column(sa.Column("outcome_status", sa.String(32), nullable=True))
        batch.add_column(sa.Column("attempt_number", sa.Integer(), nullable=True))
    op.create_index("ix_activity_logs_task_id", "activity_logs", ["task_id"])
    op.create_index("ix_activity_logs_task_step_id", "activity_logs", ["task_step_id"])
    op.create_index("ix_activity_logs_action_id", "activity_logs", ["action_id"])
    op.create_index("ix_activity_logs_attempt_id", "activity_logs", ["attempt_id"])

    op.execute(
        "UPDATE agent_runs SET task_id = ("
        "SELECT tasks.id FROM tasks WHERE tasks.run_id = agent_runs.id"
        ") WHERE task_id IS NULL"
    )
    op.execute(
        "UPDATE approvals SET task_id = ("
        "SELECT agent_runs.task_id FROM agent_runs "
        "WHERE agent_runs.id = approvals.run_id"
        ") WHERE task_id IS NULL"
    )
    op.execute(
        "UPDATE approvals SET decision_status = CASE "
        "WHEN status IN ('approved', 'rejected', 'skipped', 'expired', 'revoked') "
        "THEN status ELSE 'pending' END"
    )
    op.execute(
        "UPDATE approvals SET claim_status = CASE "
        "WHEN status = 'sending' THEN 'claimed' "
        "WHEN status IN ('approved', 'rejected', 'skipped', 'expired', 'revoked', "
        "'failed', 'outcome_unknown') THEN 'finished' "
        "ELSE 'unclaimed' END"
    )
    op.execute(
        "UPDATE activity_logs SET task_id = ("
        "SELECT agent_runs.task_id FROM agent_runs "
        "WHERE agent_runs.id = activity_logs.run_id"
        ") WHERE task_id IS NULL"
    )


def downgrade() -> None:
    for index in ("ix_activity_logs_attempt_id", "ix_activity_logs_action_id",
                  "ix_activity_logs_task_step_id", "ix_activity_logs_task_id"):
        op.drop_index(index, table_name="activity_logs")
    with op.batch_alter_table("activity_logs") as batch:
        for column in ("attempt_number", "outcome_status", "attempt_id", "action_id",
                       "task_step_id", "task_id"):
            batch.drop_column(column)
    op.drop_index("ix_approvals_task_step_id", table_name="approvals")
    op.drop_index("ix_approvals_task_id", table_name="approvals")
    with op.batch_alter_table("approvals") as batch:
        for column in ("decision_actor_id", "revoked_at", "expires_at",
                       "claimed_at", "payload_digest", "payload_version",
                       "claim_status", "decision_status", "task_step_id", "task_id"):
            batch.drop_column(column)
    op.drop_index("ix_agent_runs_task_id", table_name="agent_runs")
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_column("completed_at")
        batch.drop_column("started_at")
        batch.drop_column("attempt_number")
        batch.drop_column("task_id")
    op.drop_index("ix_task_steps_action_id", table_name="task_steps")
    with op.batch_alter_table("task_steps") as batch:
        batch.drop_constraint("uq_task_steps_task_plan_id", type_="unique")
        for column in ("completed_at", "started_at", "attempt_count", "error_code",
                       "verification_status", "outcome_status", "result_data",
                       "idempotency_key", "approval_id", "action_id", "completion_outcomes",
                       "policy_version", "policy_decision", "tool_version",
                       "capability", "dependency_step_ids", "plan_step_id"):
            batch.drop_column(column)
    op.drop_index("ix_tasks_trigger_key", table_name="tasks")
    for column in ("trigger_key", "trigger_data", "target_data", "input_data",
                   "contract_version"):
        op.drop_column("tasks", column)
    op.drop_index("ix_approval_revisions_approval_id", table_name="approval_revisions")
    op.drop_table("approval_revisions")
    op.drop_index("ix_task_step_attempts_state", table_name="task_step_attempts")
    op.drop_index("ix_task_step_attempts_run_id", table_name="task_step_attempts")
    op.drop_index("ix_task_step_attempts_step_id", table_name="task_step_attempts")
    op.drop_index("ix_task_step_attempts_task_id", table_name="task_step_attempts")
    op.drop_table("task_step_attempts")
