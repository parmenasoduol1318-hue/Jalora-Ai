from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


class SQLiteBackupError(RuntimeError):
    """A backup or restore failed validation before activation."""


def _validated_database(path: Path) -> Path:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise SQLiteBackupError(f"SQLite database is not a regular file: {resolved}")
    return resolved


def _integrity_check(connection: sqlite3.Connection) -> None:
    results = connection.execute("PRAGMA integrity_check").fetchall()
    if results != [("ok",)]:
        raise SQLiteBackupError("SQLite integrity check failed.")


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    with closing(sqlite3.connect(source)) as source_connection:
        with closing(sqlite3.connect(destination)) as destination_connection:
            source_connection.backup(destination_connection)
            _integrity_check(destination_connection)


def _temporary_database(directory: Path, prefix: str) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=".sqlite3.tmp", dir=directory)
    os.close(descriptor)
    return Path(name)


def create_sqlite_backup(source: Path, destination: Path) -> Path:
    """Create an atomic, integrity-checked backup including committed WAL data."""
    source_path = _validated_database(source)
    destination_path = destination.expanduser().resolve(strict=False)
    if source_path == destination_path:
        raise SQLiteBackupError("Backup destination must differ from the source database.")
    if destination_path.exists():
        raise FileExistsError(destination_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = _temporary_database(destination_path.parent, f".{destination_path.name}.")
    try:
        _sqlite_snapshot(source_path, temporary_path)
        if os.name == "posix":
            temporary_path.chmod(0o600)
        os.link(temporary_path, destination_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return destination_path


def restore_sqlite_backup(backup: Path, target: Path) -> Path | None:
    """Atomically restore a validated backup; return a pre-restore safety copy.

    The application must be stopped before this function is used. It preserves
    the current target in a sibling backup before replacing its database file.
    """
    backup_path = _validated_database(backup)
    target_path = target.expanduser().resolve(strict=False)
    if backup_path == target_path:
        raise SQLiteBackupError("Restore source and target must differ.")
    target_path.parent.mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(backup_path)) as backup_connection:
        _integrity_check(backup_connection)

    temporary_path = _temporary_database(target_path.parent, f".{target_path.name}.restore.")
    safety_path: Path | None = None
    sidecar_moves: list[tuple[Path, Path]] = []
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    try:
        _sqlite_snapshot(backup_path, temporary_path)
        if os.name == "posix":
            temporary_path.chmod(0o600)

        if target_path.exists():
            safety_path = target_path.with_name(
                f"{target_path.name}.pre-restore-{timestamp}.sqlite3"
            )
            create_sqlite_backup(target_path, safety_path)

        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{target_path}{suffix}")
            if sidecar.exists():
                archived = Path(f"{target_path}.pre-restore-{timestamp}{suffix}")
                sidecar.replace(archived)
                sidecar_moves.append((sidecar, archived))

        os.replace(temporary_path, target_path)
    except Exception:
        for original, archived in reversed(sidecar_moves):
            if archived.exists():
                archived.replace(original)
        raise
    finally:
        temporary_path.unlink(missing_ok=True)

    for _, archived in sidecar_moves:
        archived.unlink(missing_ok=True)
    return safety_path
