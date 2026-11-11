#!/usr/bin/env python
"""Create and restore validated SQLite backups for Jalora."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from sqlalchemy.engine import make_url

from app.config import get_settings
from app.services.sqlite_backup import (
    SQLiteBackupError,
    create_sqlite_backup,
    restore_sqlite_backup,
)


def _configured_sqlite_path(database_url: str) -> Path:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise SQLiteBackupError(
            "The backup utility supports file-based SQLite only; use the database "
            "vendor's supported backup tooling for other databases."
        )
    return Path(url.database).expanduser().resolve()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    backup = actions.add_parser("backup", help="create a consistent SQLite backup")
    backup.add_argument("--output", type=Path)
    restore = actions.add_parser("restore", help="restore a validated backup")
    restore.add_argument("backup", type=Path)
    restore.add_argument("--target", type=Path)
    restore.add_argument(
        "--confirm-offline",
        action="store_true",
        help="confirm that the Jalora backend is stopped before replacing its database",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    try:
        database_path = _configured_sqlite_path(settings.database_url)
        if args.action == "backup":
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            output = args.output or (
                settings.data_dir / "backups" / f"jalora-{timestamp}.sqlite3"
            )
            result = create_sqlite_backup(database_path, output)
            print(f"SQLite backup created and integrity-checked: {result}")
            return 0

        if not args.confirm_offline:
            print(
                "Restore refused: stop the backend and pass --confirm-offline.",
                file=sys.stderr,
            )
            return 2
        target = args.target or database_path
        safety_copy = restore_sqlite_backup(args.backup, target)
        print(f"SQLite backup restored and integrity-checked: {target.resolve()}")
        if safety_copy:
            print(f"Pre-restore safety backup: {safety_copy}")
        return 0
    except (SQLiteBackupError, OSError, sqlite3.Error, ValueError) as exc:
        print(f"SQLite backup operation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
