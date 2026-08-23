from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.native_runtime import (
    DATA_DIRECTORIES,
    NativeDatabaseMigrationError,
    backup_database_before_upgrade,
    prepare_native_data_directory,
    upgrade_native_database,
)


def test_native_data_layout_and_pre_upgrade_backup_are_idempotent(tmp_path: Path):
    data_dir = prepare_native_data_directory(tmp_path / "Application Support" / "KnowledgeDebt")
    assert all((data_dir / name).is_dir() for name in DATA_DIRECTORIES)
    assert data_dir.stat().st_mode & 0o777 == 0o700
    assert all((data_dir / name).stat().st_mode & 0o777 == 0o700 for name in DATA_DIRECTORIES)

    database = data_dir / "knowledgedebt.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE proof (value TEXT NOT NULL)")
        connection.execute("INSERT INTO proof VALUES ('retained user data')")

    first = backup_database_before_upgrade(data_dir, "0.2.0")
    assert first == data_dir / "backups" / "knowledgedebt-before-0.2.0.sqlite3"
    assert first is not None and first.is_file()
    assert first.stat().st_mode & 0o777 == 0o600
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


def test_native_production_upgrade_migrates_legacy_database_and_preserves_data(tmp_path: Path):
    data_dir = prepare_native_data_directory(tmp_path / "KnowledgeDebt")
    database = data_dir / "knowledgedebt.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE courses (
                 id TEXT PRIMARY KEY, name TEXT, description TEXT, semester TEXT, teacher TEXT,
                 schedule TEXT, profile_json TEXT, created_at TEXT, updated_at TEXT
               )"""
        )
        connection.execute(
            "INSERT INTO courses VALUES ('old', 'Legacy', '', '', NULL, NULL, ?, 'old', 'old')",
            (json.dumps({"audio": 100}),),
        )

    backend_dir = Path(__file__).resolve().parents[1]
    backup = upgrade_native_database(
        data_dir,
        "0.3.0",
        script_location=backend_dir / "alembic",
    )

    assert backup == data_dir / "backups/knowledgedebt-before-0.3.0.sqlite3"
    assert backup is not None and backup.is_file()
    assert database.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT name FROM courses WHERE id='old'").fetchone()[0] == "Legacy"
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        ).fetchone() is None
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT name FROM courses WHERE id='old'").fetchone()[0] == "Legacy"
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260823_0008"
        occurrence_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(schedule_occurrences)")
        }
        assert "adjustment_external_id" in occurrence_columns
        profile = json.loads(
            connection.execute("SELECT profile_json FROM courses WHERE id='old'").fetchone()[0]
        )
        assert set(profile) == {
            "classroom",
            "official_session",
            "course_context",
            "supplementary",
        }


def test_native_migration_failure_stops_and_retains_pre_upgrade_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    data_dir = prepare_native_data_directory(tmp_path / "KnowledgeDebt")
    database = data_dir / "knowledgedebt.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE proof (value TEXT NOT NULL)")
        connection.execute("INSERT INTO proof VALUES ('must survive')")

    def fail_upgrade(*_args, **_kwargs):
        raise RuntimeError("simulated migration failure")

    monkeypatch.setattr("app.native_runtime.command.upgrade", fail_upgrade)
    with pytest.raises(NativeDatabaseMigrationError) as error:
        upgrade_native_database(
            data_dir,
            "0.3.1",
            script_location=Path(__file__).resolve().parents[1] / "alembic",
        )

    backup = data_dir / "backups/knowledgedebt-before-0.3.1.sqlite3"
    assert error.value.backup == backup
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT value FROM proof").fetchone()[0] == "must survive"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM proof").fetchone()[0] == "must survive"
