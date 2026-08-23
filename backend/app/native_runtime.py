from __future__ import annotations

import os
import re
import sqlite3
import sys
import uuid
from pathlib import Path

from alembic.config import Config

from alembic import command

DATA_DIRECTORIES = (
    "backups",
    "diagnostics",
    "derived",
    "logs",
    "models",
    "recording-chunks",
    "resources",
    "runtime-state",
    "secrets",
    "transcription-chunks",
)


class NativeDatabaseMigrationError(RuntimeError):
    def __init__(self, database: Path, backup: Path | None):
        backup_message = f"升级前备份保留在 {backup}" if backup else "这是一个尚无旧数据的新数据库"
        super().__init__(f"本地数据库升级失败，应用已停止启动；{backup_message}。数据库：{database}")
        self.database = database
        self.backup = backup


def prepare_native_data_directory(root: Path) -> Path:
    """Create the persistent macOS data layout without touching existing user files."""

    resolved = root.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    resolved.chmod(0o700)
    for name in DATA_DIRECTORIES:
        directory = resolved / name
        directory.mkdir(exist_ok=True)
        directory.chmod(0o700)
    return resolved


def backup_database_before_upgrade(root: Path, version: str) -> Path | None:
    """Create one consistent SQLite backup per target version before schema initialization."""

    source = root / "knowledgedebt.sqlite3"
    if not source.is_file() or source.stat().st_size == 0:
        return None
    safe_version = re.sub(r"[^A-Za-z0-9._-]", "_", version) or "unknown"
    target = root / "backups" / f"knowledgedebt-before-{safe_version}.sqlite3"
    if target.is_file() and target.stat().st_size > 0:
        target.chmod(0o600)
        return target
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as existing:
            with sqlite3.connect(temporary) as backup:
                existing.backup(backup)
        if temporary.stat().st_size == 0:
            raise OSError("数据库升级备份为空")
        os.replace(temporary, target)
        target.chmod(0o600)
        return target
    finally:
        temporary.unlink(missing_ok=True)


def native_alembic_directory() -> Path:
    """Locate migrations in both the source tree and the frozen macOS bundle."""

    candidates = (
        Path(sys.executable).resolve().parent / "alembic",
        Path(__file__).resolve().parents[1] / "alembic",
    )
    for candidate in candidates:
        if (candidate / "env.py").is_file() and (candidate / "versions").is_dir():
            return candidate
    raise FileNotFoundError("应用缺少数据库迁移文件，请重新安装 KnowledgeDebt。")


def upgrade_native_database(
    root: Path,
    version: str,
    *,
    script_location: Path | None = None,
) -> Path | None:
    """Back up and migrate the native SQLite database before app construction.

    Alembic is the authoritative native upgrade path. ``Database`` keeps its legacy
    initializer only for source-mode/test bootstrap compatibility; native startup
    always reaches Alembic head first and aborts on any failure.
    """

    data_dir = prepare_native_data_directory(root)
    database = data_dir / "knowledgedebt.sqlite3"
    backup = backup_database_before_upgrade(data_dir, version)
    migrations = (script_location or native_alembic_directory()).resolve()
    config = Config()
    config.set_main_option("script_location", str(migrations).replace("%", "%%"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}".replace("%", "%%"))
    try:
        command.upgrade(config, "head")
    except Exception as exc:
        raise NativeDatabaseMigrationError(database, backup) from exc
    database.chmod(0o600)
    return backup
