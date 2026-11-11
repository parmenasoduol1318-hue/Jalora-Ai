"""Persist schedule occurrences and enforce a single scheduler owner."""

import sqlalchemy as sa
from alembic import op


revision = "v1i_schedule_occurrence_lifecycle"
down_revision = "v1h_workflow_canonical_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schedule_occurrences",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("schedule_id", sa.String(32), nullable=False),
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("occurrence_key", sa.String(64), nullable=False, unique=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="dispatching"),
        sa.Column("task_template", sa.Text(), nullable=False, server_default=""),
        sa.Column("notify_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("task_id", sa.String(32), nullable=True),
        sa.Column("run_id", sa.String(32), nullable=True),
        sa.Column("notification_id", sa.String(32), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "schedule_id", "occurrence_key",
            name="uq_schedule_occurrences_schedule_key",
        ),
    )
    op.create_index(
        "ix_schedule_occurrences_schedule_id",
        "schedule_occurrences", ["schedule_id"],
    )
    op.create_index(
        "ix_schedule_occurrences_user_id",
        "schedule_occurrences", ["user_id"],
    )
    op.create_index(
        "ix_schedule_occurrences_scheduled_for",
        "schedule_occurrences", ["scheduled_for"],
    )
    op.create_index(
        "ix_schedule_occurrences_status",
        "schedule_occurrences", ["status"],
    )
    op.create_index(
        "ix_schedule_occurrences_task_id",
        "schedule_occurrences", ["task_id"],
    )
    op.create_index(
        "ix_schedule_occurrences_run_id",
        "schedule_occurrences", ["run_id"],
    )
    op.create_table(
        "scheduler_leases",
        sa.Column("name", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_scheduler_leases_owner_id", "scheduler_leases", ["owner_id"],
    )
    op.create_index(
        "ix_scheduler_leases_lease_expires_at",
        "scheduler_leases", ["lease_expires_at"],
    )
    with op.batch_alter_table("schedules") as batch:
        batch.add_column(
            sa.Column("schedule_expression", sa.String(200), nullable=False, server_default="")
        )
    with op.batch_alter_table("notifications") as batch:
        batch.add_column(sa.Column("occurrence_id", sa.String(32), nullable=True))
        batch.create_unique_constraint(
            "uq_notifications_occurrence_id", ["occurrence_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("uq_notifications_occurrence_id", type_="unique")
        batch.drop_column("occurrence_id")
    with op.batch_alter_table("schedules") as batch:
        batch.drop_column("schedule_expression")
    op.drop_index("ix_scheduler_leases_lease_expires_at", table_name="scheduler_leases")
    op.drop_index("ix_scheduler_leases_owner_id", table_name="scheduler_leases")
    op.drop_table("scheduler_leases")
    op.drop_index("ix_schedule_occurrences_run_id", table_name="schedule_occurrences")
    op.drop_index("ix_schedule_occurrences_task_id", table_name="schedule_occurrences")
    op.drop_index("ix_schedule_occurrences_status", table_name="schedule_occurrences")
    op.drop_index("ix_schedule_occurrences_scheduled_for", table_name="schedule_occurrences")
    op.drop_index("ix_schedule_occurrences_user_id", table_name="schedule_occurrences")
    op.drop_index("ix_schedule_occurrences_schedule_id", table_name="schedule_occurrences")
    op.drop_table("schedule_occurrences")
