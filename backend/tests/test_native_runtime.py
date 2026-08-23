from __future__ import annotations

import sqlite3
from pathlib import Path

from app.native_runtime import (
    DATA_DIRECTORIES,
    backup_database_before_upgrade,
    prepare_native_data_directory,
)


def test_native_data_layout_and_pre_upgrade_backup_are_idempotent(tmp_path: Path):
    data_dir = prepare_native_data_directory(tmp_path / "Application Support" / "KnowledgeDebt")
    assert all((data_dir / name).is_dir() for name in DATA_DIRECTORIES)

    database = data_dir / "knowledgedebt.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE proof (value TEXT NOT NULL)")
        connection.execute("INSERT INTO proof VALUES ('retained user data')")

    first = backup_database_before_upgrade(data_dir, "0.2.0")
    assert first == data_dir / "backups" / "knowledgedebt-before-0.2.0.sqlite3"
    assert first is not None and first.is_file()
    with sqlite3.connect(first) as backup:
        assert backup.execute("SELECT value FROM proof").fetchone()[0] == "retained user data"

    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO proof VALUES ('newer launch')")
    second = backup_database_before_upgrade(data_dir, "0.2.0")
    assert second == first
    with sqlite3.connect(second) as backup:
        assert backup.execute("SELECT COUNT(*) FROM proof").fetchone()[0] == 1


def test_native_backup_ignores_missing_database(tmp_path: Path):
    data_dir = prepare_native_data_directory(tmp_path / "KnowledgeDebt")
    assert backup_database_before_upgrade(data_dir, "0.2.0") is None
