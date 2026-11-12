"""Alembic migration lifecycle: upgrade, legacy adoption (stamp), round-trip."""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timedelta, timezone

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from app.database.base import Base
from app.database.session import SessionLocal, engine, init_db

CFG = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
MODEL_TABLES = {"users", "conversations", "messages", "memories", "documents",
                "document_chunks", "agent_runs", "approvals", "activity_logs",
                "refresh_tokens", "feedback", "workflow_patterns", "tasks",
                "task_steps", "task_step_attempts", "approval_revisions",
                "schedule_occurrences", "scheduler_leases"}


def _tables() -> set[str]:
    with engine.connect() as conn:
        return set(inspect(conn).get_table_names())


def _reset() -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    Base.metadata.drop_all(bind=engine)


def test_upgrade_on_empty_db_creates_full_schema():
    _reset()
    assert not _tables()
    init_db()
    tables = _tables()
    assert MODEL_TABLES <= tables, tables
    assert "alembic_version" in tables


def test_legacy_create_all_db_is_stamped_head():
    _reset()
    Base.metadata.create_all(bind=engine)  # pre-Alembic legacy database
    assert "alembic_version" not in _tables()

    init_db()  # must stamp, not re-run DDL against existing tables

    head = ScriptDirectory.from_config(CFG).get_current_head()
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version == head


def test_older_unversioned_schema_is_not_stamped_as_head():
    _reset()
    command.upgrade(CFG, "v1f_approval_plan_step")
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))

    try:
        init_db()
    except RuntimeError as exc:
        assert "tasks.contract_version" in str(exc)
    else:
        raise AssertionError("an older schema must not be stamped as current")

    assert "alembic_version" not in _tables()


def test_downgrade_base_then_upgrade_roundtrip():
    _reset()
    init_db()
    command.downgrade(CFG, "base")
    tables = _tables()
    assert not (MODEL_TABLES & tables), tables

    init_db()  # empty again -> upgrade
    assert MODEL_TABLES <= _tables()


def test_protocol_migration_backfills_existing_remote_commands():
    _reset()
    command.upgrade(CFG, "v1i_schedule_occurrence_lifecycle")
    now = datetime.now(timezone.utc)
    user_id = "protocol-migration-user"
    device_id = "protocol-migration-device"
    command_id = "protocol-migration-command"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users "
                "(id, email, name, password_hash, is_active, prefs, created_at) "
                "VALUES (:id, :email, :name, :password_hash, :is_active, :prefs, :created_at)"
            ),
            {
                "id": user_id,
                "email": "protocol-migration@test.com",
                "name": "Protocol migration",
                "password_hash": "unused",
                "is_active": True,
                "prefs": "{}",
                "created_at": now.isoformat(),
            },
        )
        conn.execute(
            text(
                "INSERT INTO device_connections "
                "(id, user_id, name, platform, status, token_hash, scopes, created_at) "
                "VALUES (:id, :user_id, :name, :platform, :status, :token_hash, :scopes, :created_at)"
            ),
            {
                "id": device_id,
                "user_id": user_id,
                "name": "Migration device",
                "platform": "android",
                "status": "active",
                "token_hash": "0" * 64,
                "scopes": json.dumps(["remote.browser.open_url"]),
                "created_at": now.isoformat(),
            },
        )
        conn.execute(
            text(
                "INSERT INTO device_remote_commands "
                "(id, user_id, device_id, action, target_url, status, created_at, expires_at) "
                "VALUES (:id, :user_id, :device_id, :action, :target_url, :status, :created_at, :expires_at)"
            ),
            {
                "id": command_id,
                "user_id": user_id,
                "device_id": device_id,
                "action": "browser.open_url",
                "target_url": "https://example.com/migrated",
                "status": "pending",
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=5)).isoformat(),
            },
        )

    command.upgrade(CFG, "head")
    from app.models.device import DeviceRemoteCommand
    from app.services.device_command_protocol import command_envelope

    with SessionLocal() as db:
        migrated = db.get(DeviceRemoteCommand, command_id)
        assert migrated is not None
        envelope = command_envelope(migrated)
        assert envelope["protocol_version"] == 1
        assert envelope["arguments"] == {
            "url": "https://example.com/migrated"
        }
        assert envelope["task_id"] is None
        assert migrated.protocol_status == "queued"


def test_windows_companion_migration_adds_capability_manifest():
    _reset()
    command.upgrade(CFG, "v1j_device_command_protocol")
    assert "capability_manifest" not in {
        column["name"] for column in inspect(engine).get_columns("device_connections")
    }

    command.upgrade(CFG, "head")

    assert "capability_manifest" in {
        column["name"] for column in inspect(engine).get_columns("device_connections")
    }
