from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from app.services.sqlite_backup import (
    create_sqlite_backup,
    restore_sqlite_backup,
)


def _create_database(path: Path, value: str) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample (value) VALUES (?)", (value,))
        connection.commit()


def _read_value(path: Path) -> str:
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("SELECT value FROM sample").fetchone()[0]


def test_backup_includes_committed_wal_data_and_refuses_overwrite(tmp_path):
    source = tmp_path / "source.sqlite3"
    destination = tmp_path / "backups" / "snapshot.sqlite3"
    source_connection = sqlite3.connect(source)
    source_connection.execute("PRAGMA journal_mode=WAL")
    source_connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
    source_connection.execute("INSERT INTO sample (value) VALUES ('committed')")
    source_connection.commit()
    try:
        result = create_sqlite_backup(source, destination)
    finally:
        source_connection.close()

    assert result == destination
    assert _read_value(destination) == "committed"
    with pytest.raises(FileExistsError):
        create_sqlite_backup(source, destination)


def test_restore_validates_snapshot_and_preserves_pre_restore_database(tmp_path):
    backup_source = tmp_path / "backup-source.sqlite3"
    target = tmp_path / "live.sqlite3"
    _create_database(backup_source, "restored")
    _create_database(target, "before")
    backup = create_sqlite_backup(backup_source, tmp_path / "backup.sqlite3")

    safety_copy = restore_sqlite_backup(backup, target)

    assert safety_copy is not None
    assert safety_copy.exists()
    assert _read_value(target) == "restored"
    assert _read_value(safety_copy) == "before"


def test_restore_rejects_invalid_backup_without_changing_target(tmp_path):
    invalid_backup = tmp_path / "invalid.sqlite3"
    invalid_backup.write_bytes(b"not a sqlite database")
    target = tmp_path / "live.sqlite3"
    _create_database(target, "keep")

    with pytest.raises(sqlite3.DatabaseError):
        restore_sqlite_backup(invalid_backup, target)

    assert _read_value(target) == "keep"
