from __future__ import annotations

import os
import re
import sqlite3
import uuid
from pathlib import Path

DATA_DIRECTORIES = (
    "backups",
    "derived",
    "logs",
    "models",
    "recording-chunks",
    "resources",
    "runtime-state",
    "secrets",
    "transcription-chunks",
)


def prepare_native_data_directory(root: Path) -> Path:
    """Create the persistent macOS data layout without touching existing user files."""

    resolved = root.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    for name in DATA_DIRECTORIES:
        (resolved / name).mkdir(exist_ok=True)
    return resolved


def backup_database_before_upgrade(root: Path, version: str) -> Path | None:
    """Create one consistent SQLite backup per target version before schema initialization."""

    source = root / "knowledgedebt.sqlite3"
    if not source.is_file() or source.stat().st_size == 0:
        return None
    safe_version = re.sub(r"[^A-Za-z0-9._-]", "_", version) or "unknown"
    target = root / "backups" / f"knowledgedebt-before-{safe_version}.sqlite3"
    if target.is_file() and target.stat().st_size > 0:
        return target
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as existing:
            with sqlite3.connect(temporary) as backup:
                existing.backup(backup)
        if temporary.stat().st_size == 0:
            raise OSError("数据库升级备份为空")
        os.replace(temporary, target)
        return target
    finally:
        temporary.unlink(missing_ok=True)
