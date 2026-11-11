from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.database.base import Base

_settings = get_settings()

connect_args: dict = {}
if _settings.database_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    _settings.database_url,
    connect_args=connect_args,
    pool_pre_ping=True,
    future=True,
)

if _settings.database_url.startswith("sqlite"):

    @event.listens_for(Engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # pragma: no cover
        cursor = dbapi_connection.cursor()
        # Enable WAL mode for better read concurrency
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")  # Faster writes
        cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
        cursor.execute("PRAGMA temp_store=MEMORY")  # Keep temp tables in memory
        cursor.execute("PRAGMA max_page_count=2147483646")  # Increase max DB size
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """Bring the schema to head via Alembic migrations.

    - empty database        -> `alembic upgrade head` (creates schema)
    - legacy pre-Alembic DB -> `alembic stamp head` (tables already exist)
    - tracked database      -> `alembic upgrade head`
    """
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    from app import models  # noqa: F401 - registers every model on metadata

    with engine.connect() as conn:
        inspector = inspect(conn)
        tables = inspector.get_table_names()
        columns_by_table = {
            table_name: {
                column["name"] for column in inspector.get_columns(table_name)
            }
            for table_name in tables
        }

    backend_dir = Path(__file__).resolve().parents[2]  # .../backend
    cfg = Config(str(backend_dir / "alembic.ini"))

    if not tables:
        command.upgrade(cfg, "head")
    elif "alembic_version" not in tables:
        # Only adopt create_all databases whose canonical lifecycle schema
        # already matches the current model contract. Stamping an older schema
        # would permanently skip additive migrations.
        required_columns = {
            "tasks": {"contract_version", "input_data", "trigger_data", "trigger_key"},
            "task_steps": {
                "plan_step_id", "dependency_step_ids", "action_id", "approval_id", "outcome_status",
                "verification_status", "attempt_count",
            },
            "agent_runs": {"task_id", "attempt_number", "started_at", "completed_at"},
            "approvals": {"task_id", "task_step_id", "decision_status", "claim_status"},
            "activity_logs": {"task_id", "task_step_id", "attempt_id"},
            "schedules": {"schedule_expression"},
            "notifications": {"occurrence_id"},
        }
        missing: list[str] = []
        for table_name, expected in required_columns.items():
            if table_name not in tables:
                missing.append(f"{table_name} (table)")
                continue
            actual = columns_by_table[table_name]
            missing.extend(
                f"{table_name}.{column}" for column in sorted(expected - actual)
            )
        for table_name in (
            "task_step_attempts", "approval_revisions",
            "schedule_occurrences", "scheduler_leases",
        ):
            if table_name not in tables:
                missing.append(f"{table_name} (table)")
        if missing:
            raise RuntimeError(
                "Cannot adopt this unversioned database: its schema is older than "
                "the current execution lifecycle. Missing: " + ", ".join(missing)
                + ". Back up the database and run the supported Alembic migrations."
            )
        command.stamp(cfg, "head")
    else:
        command.upgrade(cfg, "head")


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
