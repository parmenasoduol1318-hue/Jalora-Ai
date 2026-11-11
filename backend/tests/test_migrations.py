"""Alembic migration lifecycle: upgrade, legacy adoption (stamp), round-trip."""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from app.database.base import Base
from app.database.session import engine, init_db

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
