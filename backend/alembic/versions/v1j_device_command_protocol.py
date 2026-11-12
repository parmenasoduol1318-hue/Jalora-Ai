"""Add a versioned, idempotent outbound device-command protocol."""

import hashlib
import json
import secrets
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "v1j_device_command_protocol"
down_revision: Union[str, Sequence[str], None] = "v1i_schedule_occurrence_lifecycle"
branch_labels = None
depends_on = None


def _legacy_transport_status(status: str) -> str:
    return {
        "opened": "succeeded",
        "failed": "failed",
        "rejected": "cancelled",
        "cancelled": "cancelled",
        "expired": "expired",
        "approved": "uncertain",
    }.get(status, "queued")


def upgrade() -> None:
    op.add_column(
        "device_remote_commands",
        sa.Column("protocol_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "device_remote_commands",
        sa.Column("arguments", sa.JSON(), nullable=True),
    )
    op.add_column(
        "device_remote_commands",
        sa.Column(
            "required_scope",
            sa.String(64),
            nullable=False,
            server_default="remote.browser.open_url",
        ),
    )
    op.add_column(
        "device_remote_commands",
        sa.Column("command_nonce", sa.String(64), nullable=True),
    )
    op.add_column(
        "device_remote_commands",
        sa.Column("idempotency_key", sa.String(64), nullable=True),
    )
    op.add_column(
        "device_remote_commands",
        sa.Column("payload_digest", sa.String(64), nullable=True),
    )
    op.add_column(
        "device_remote_commands",
        sa.Column("expected_evidence", sa.JSON(), nullable=True),
    )
    op.add_column(
        "device_remote_commands",
        sa.Column("task_id", sa.String(32), nullable=True),
    )
    op.add_column(
        "device_remote_commands",
        sa.Column("run_id", sa.String(32), nullable=True),
    )
    op.add_column(
        "device_remote_commands",
        sa.Column("task_step_id", sa.String(32), nullable=True),
    )
    op.add_column(
        "device_remote_commands",
        sa.Column(
            "protocol_status",
            sa.String(16),
            nullable=False,
            server_default="queued",
        ),
    )
    op.add_column(
        "device_remote_commands",
        sa.Column(
            "delivery_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "device_remote_commands",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "device_remote_commands",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "device_remote_commands",
        sa.Column("running_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "device_remote_commands",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "device_remote_commands",
        sa.Column("receipt", sa.JSON(), nullable=True),
    )
    op.add_column(
        "device_remote_commands",
        sa.Column("error_code", sa.String(64), nullable=True),
    )

    commands = sa.Table(
        "device_remote_commands",
        sa.MetaData(),
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32)),
        sa.Column("device_id", sa.String(32)),
        sa.Column("action", sa.String(32)),
        sa.Column("target_url", sa.Text()),
        sa.Column("status", sa.String(16)),
        sa.Column("arguments", sa.JSON()),
        sa.Column("command_nonce", sa.String(64)),
        sa.Column("idempotency_key", sa.String(64)),
        sa.Column("payload_digest", sa.String(64)),
        sa.Column("expected_evidence", sa.JSON()),
        sa.Column("protocol_status", sa.String(16)),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(
            commands.c.id,
            commands.c.user_id,
            commands.c.device_id,
            commands.c.action,
            commands.c.target_url,
            commands.c.status,
        )
    ).mappings().all()
    for row in rows:
        arguments = {"url": row["target_url"]}
        digest_payload = {
            "action": row["action"],
            "arguments": arguments,
            "device_id": row["device_id"],
            "required_scope": "remote.browser.open_url",
            "run_id": None,
            "task_id": None,
            "task_step_id": None,
            "user_id": row["user_id"],
        }
        payload_digest = hashlib.sha256(
            json.dumps(
                digest_payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        idempotency_key = hashlib.sha256(
            f"legacy-device-command:{row['id']}".encode("utf-8")
        ).hexdigest()
        connection.execute(
            sa.update(commands)
            .where(commands.c.id == row["id"])
            .values(
                arguments=arguments,
                command_nonce=secrets.token_urlsafe(32),
                idempotency_key=idempotency_key,
                payload_digest=payload_digest,
                expected_evidence=["target_approval", "browser_open_receipt"],
                protocol_status=_legacy_transport_status(row["status"]),
            )
        )

    with op.batch_alter_table("device_remote_commands") as batch:
        batch.alter_column(
            "arguments",
            existing_type=sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        )
        batch.alter_column(
            "command_nonce", existing_type=sa.String(64), nullable=False
        )
        batch.alter_column(
            "idempotency_key", existing_type=sa.String(64), nullable=False
        )
        batch.alter_column(
            "payload_digest", existing_type=sa.String(64), nullable=False
        )
        batch.alter_column(
            "expected_evidence",
            existing_type=sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        )
        batch.create_foreign_key(
            "fk_device_remote_commands_task_id_tasks",
            "tasks",
            ["task_id"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_device_remote_commands_run_id_agent_runs",
            "agent_runs",
            ["run_id"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_device_remote_commands_task_step_id_task_steps",
            "task_steps",
            ["task_step_id"],
            ["id"],
        )
        batch.create_unique_constraint(
            "uq_device_remote_commands_command_nonce", ["command_nonce"]
        )
        batch.create_unique_constraint(
            "uq_device_remote_commands_user_idempotency",
            ["user_id", "idempotency_key"],
        )

    op.create_index(
        "ix_device_remote_commands_task_id",
        "device_remote_commands",
        ["task_id"],
    )
    op.create_index(
        "ix_device_remote_commands_run_id",
        "device_remote_commands",
        ["run_id"],
    )
    op.create_index(
        "ix_device_remote_commands_task_step_id",
        "device_remote_commands",
        ["task_step_id"],
    )
    op.create_index(
        "ix_device_remote_commands_protocol_status",
        "device_remote_commands",
        ["protocol_status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_device_remote_commands_protocol_status",
        table_name="device_remote_commands",
    )
    op.drop_index(
        "ix_device_remote_commands_task_step_id",
        table_name="device_remote_commands",
    )
    op.drop_index(
        "ix_device_remote_commands_run_id",
        table_name="device_remote_commands",
    )
    op.drop_index(
        "ix_device_remote_commands_task_id",
        table_name="device_remote_commands",
    )
    with op.batch_alter_table("device_remote_commands") as batch:
        batch.drop_constraint(
            "uq_device_remote_commands_user_idempotency", type_="unique"
        )
        batch.drop_constraint(
            "uq_device_remote_commands_command_nonce", type_="unique"
        )
        batch.drop_constraint(
            "fk_device_remote_commands_task_step_id_task_steps",
            type_="foreignkey",
        )
        batch.drop_constraint(
            "fk_device_remote_commands_run_id_agent_runs",
            type_="foreignkey",
        )
        batch.drop_constraint(
            "fk_device_remote_commands_task_id_tasks", type_="foreignkey"
        )
        for column in (
            "error_code",
            "receipt",
            "completed_at",
            "running_at",
            "lease_expires_at",
            "delivered_at",
            "delivery_count",
            "protocol_status",
            "task_step_id",
            "run_id",
            "task_id",
            "expected_evidence",
            "payload_digest",
            "idempotency_key",
            "command_nonce",
            "required_scope",
            "arguments",
            "protocol_version",
        ):
            batch.drop_column(column)
