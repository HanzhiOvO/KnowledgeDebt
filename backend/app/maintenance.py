from __future__ import annotations

import json
import logging
import os
import re
import stat
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .automation import AutomationRepository
from .database import Database
from .models import utc_now
from .recordings import RecordingManager
from .storage.base import StoredObject

logger = logging.getLogger(__name__)


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


class ChunkCleanupError(OSError):
    def __init__(self, message: str, deleted_bytes: int) -> None:
        super().__init__(message)
        self.deleted_bytes = deleted_bytes


class StorageMaintenance:
    """Explicit, conservative maintenance for local data.

    Merely reading other pages never traverses the data directory. A refresh performs the
    expensive validation, and cleanup always revalidates every selected recording.
    """

    _SAFE_RECORDING_ID = re.compile(r"^[A-Za-z0-9_-]{8,100}$")
    _SCAN_LIMIT = 100_000
    _PREVIEW_LIMIT = 128
    _RESULT_LIMIT = 256
    _RESULT_TTL = timedelta(hours=1)

    def __init__(
        self,
        db: Database,
        automation: AutomationRepository,
        recordings: RecordingManager,
        data_dir: Path,
    ) -> None:
        self.db = db
        self.automation = automation
        self.recordings = recordings
        self.data_dir = data_dir.resolve()
        self._snapshot: dict[str, Any] | None = None
        self._previews: dict[str, dict[str, Any]] = {}
        self._cleanup_results: dict[str, dict[str, Any]] = {}
        self._cleanup_lock = threading.Lock()

    def cached(self) -> dict[str, Any]:
        if self._snapshot is None:
            return {
                "generated_at": None,
                "cached": True,
                "needs_refresh": True,
                "retention_days": self._retention(),
                "categories": [],
                "cleanup": {"eligible_recordings": 0, "eligible_bytes": 0},
            }
        return {**self._snapshot, "cached": True, "needs_refresh": False}

    def refresh(self) -> dict[str, Any]:
        retention = self._retention()
        now = datetime.now(UTC)
        recording_rows = self._recording_rows()
        final_bytes = 0
        chunk_bytes = 0
        eligible_bytes = 0
        eligible_count = 0
        protected_count = 0
        reasons: dict[str, int] = {}
        for row in recording_rows:
            chunk_bytes += int(row["chunk_bytes"] or 0)
            qualification = self._qualify(row, retention, now, establish_grace=True)
            reason = qualification["reason"]
            reasons[reason] = reasons.get(reason, 0) + 1
            if qualification["eligible"]:
                eligible_count += 1
                eligible_bytes += int(row["chunk_bytes"] or 0)
            elif int(row["chunk_count"] or 0):
                protected_count += 1
            if qualification.get("final_size"):
                final_bytes += int(qualification["final_size"])

        categories = [
            self._category("录音成品", final_bytes, sum(1 for r in recording_rows if r["resource_id"])),
            self._category("录音原始分片", chunk_bytes, sum(int(r["chunk_count"] or 0) for r in recording_rows)),
        ]
        for key, label, directories in (
            ("models", "本地模型", ("models",)),
            ("logs", "运行日志", ("logs",)),
            ("backups", "数据库备份", ("backups",)),
            ("derived", "转写与派生文件", ("derived", "transcription-chunks")),
        ):
            size, count, truncated = self._scan(directories)
            categories.append(self._category(label, size, count, key=key, truncated=truncated))
        known = {"models", "logs", "backups", "derived", "transcription-chunks", "resources", "recording-chunks"}
        other_dirs = tuple(
            item.name for item in self.data_dir.iterdir()
            if item.is_dir() and not item.is_symlink() and item.name not in known
        )
        other_size, other_count, other_truncated = self._scan(other_dirs, include_root_files=True)
        categories.append(self._category("其他本地数据", other_size, other_count, key="other", truncated=other_truncated))
        snapshot = {
            "generated_at": utc_now(),
            "cached": False,
            "needs_refresh": False,
            "retention_days": retention,
            "categories": categories,
            "cleanup": {
                "eligible_recordings": eligible_count,
                "eligible_bytes": eligible_bytes,
                "protected_recordings": protected_count,
                "reasons": reasons,
            },
        }
        self._snapshot = snapshot
        return snapshot

    def preview(self) -> dict[str, Any]:
        snapshot = self.refresh()
        retention = snapshot["retention_days"]
        now = datetime.now(UTC)
        eligible = []
        for row in self._recording_rows():
            qualified = self._qualify(row, retention, now, establish_grace=False)
            if qualified["eligible"]:
                eligible.append({"recording_id": row["id"], "bytes": int(row["chunk_bytes"] or 0)})
        preview_id = uuid.uuid4().hex
        self._prune(now)
        if len(self._previews) >= self._PREVIEW_LIMIT:
            oldest = min(self._previews, key=lambda key: self._previews[key]["expires_at"])
            self._previews.pop(oldest, None)
        self._previews[preview_id] = {
            "recordings": eligible,
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
        }
        return {
            "preview_id": preview_id,
            "expires_at": self._previews[preview_id]["expires_at"],
            "recording_count": len(eligible),
            "bytes": sum(item["bytes"] for item in eligible),
            "retention_days": retention,
        }

    def cleanup(self, preview_id: str, *, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise PermissionError("请先明确确认清理已校验录音的原始分片。")
        with self._cleanup_lock:
            return self._cleanup_locked(preview_id)

    def _cleanup_locked(self, preview_id: str) -> dict[str, Any]:
        now_instant = datetime.now(UTC)
        self._prune(now_instant)
        prior = self._cleanup_results.get(preview_id)
        if prior:
            return {**prior["result"], "replayed": True}
        preview = self._previews.pop(preview_id, None)
        if not preview or _instant(preview["expires_at"]) <= now_instant:
            raise ValueError("清理预览已过期，请重新预览。")
        retention = self._retention()
        cleaned = 0
        reclaimed = 0
        failures: list[dict[str, Any]] = []
        already_clean = 0
        for candidate in preview["recordings"]:
            recording_id = candidate["recording_id"]
            deleted = 0
            try:
                with self.recordings._recording_lock(recording_id):
                    row = self._recording_row(recording_id)
                    if row is None:
                        raise ValueError("录音记录不存在")
                    qualified = self._qualify(
                        row, retention, datetime.now(UTC), establish_grace=False
                    )
                    if not qualified["eligible"]:
                        if int(row["chunk_count"] or 0) == 0:
                            already_clean += 1
                            continue
                        raise ValueError(f"已不符合清理条件：{qualified['reason']}")
                    deleted = self._delete_chunks(recording_id)
                    now = utc_now()
                    with self.db.connect(immediate=True) as conn:
                        conn.execute("DELETE FROM recording_chunks WHERE recording_id=?", (recording_id,))
                        conn.execute(
                            """UPDATE recordings SET raw_chunks_purged_at=?,
                               raw_chunks_purged_bytes=?, updated_at=? WHERE id=?""",
                            (now, deleted, now, recording_id),
                        )
                        conn.execute(
                            """INSERT INTO audit_log
                               (id, action, subject_type, subject_id, payload_json, created_at)
                               VALUES (?, 'cleanup_recording_chunks', 'recording', ?, ?, ?)""",
                            (
                                str(uuid.uuid4()),
                                recording_id,
                                json.dumps(
                                    {"bytes": deleted, "retention_days": retention},
                                    ensure_ascii=False,
                                ),
                                now,
                            ),
                        )
                    cleaned += 1
                    reclaimed += deleted
            except Exception as exc:
                physically_deleted = max(deleted, getattr(exc, "deleted_bytes", 0))
                reclaimed += physically_deleted
                code, message = self._public_failure(exc)
                logger.warning(
                    "Recording chunk cleanup did not complete recording_id=%s error_type=%s",
                    recording_id,
                    type(exc).__name__,
                )
                failures.append(
                    {
                        "recording_id": recording_id,
                        "code": code,
                        "reason": message,
                        "reclaimed_bytes": physically_deleted,
                        "recoverable": True,
                    }
                )
        self._snapshot = None
        result = {
            "cleaned_recordings": cleaned,
            "reclaimed_bytes": reclaimed,
            "already_clean": already_clean,
            "failures": failures,
            "replayed": False,
        }
        if len(self._cleanup_results) >= self._RESULT_LIMIT:
            oldest = min(
                self._cleanup_results,
                key=lambda key: self._cleanup_results[key]["expires_at"],
            )
            self._cleanup_results.pop(oldest, None)
        self._cleanup_results[preview_id] = {
            "result": result,
            "expires_at": (datetime.now(UTC) + self._RESULT_TTL).isoformat(),
        }
        return result

    @staticmethod
    def _public_failure(exc: Exception) -> tuple[str, str]:
        if isinstance(exc, ChunkCleanupError):
            return (
                "cleanup_interrupted",
                "分片清理被中断，将在下次清理时继续恢复。",
            )
        if isinstance(exc, (FileNotFoundError, FileExistsError, NotADirectoryError)):
            return (
                "chunk_storage_missing",
                "分片存储目录不可用，本条录音未完成清理。",
            )
        if isinstance(exc, PermissionError):
            return (
                "chunk_storage_unavailable",
                "分片存储暂时无法访问，本条录音未完成清理。",
            )
        if isinstance(exc, (ValueError, OSError)):
            return (
                "unsafe_chunk_storage",
                "分片存储未通过安全校验，已跳过本条录音。",
            )
        return (
            "metadata_update_failed",
            "清理登记未能完成，将在下次清理时继续恢复。",
        )

    def _prune(self, now: datetime) -> None:
        self._previews = {
            key: value
            for key, value in self._previews.items()
            if _instant(value["expires_at"]) > now
        }
        self._cleanup_results = {
            key: value
            for key, value in self._cleanup_results.items()
            if _instant(value["expires_at"]) > now
        }

    def _retention(self) -> int | None:
        return self.automation.get_app_settings()["recording_chunk_retention_days"]

    def _recording_rows(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT r.*,
                          (SELECT COUNT(*) FROM recording_chunks c WHERE c.recording_id=r.id)
                            AS chunk_count,
                          (SELECT COALESCE(SUM(c.byte_size), 0) FROM recording_chunks c
                           WHERE c.recording_id=r.id) AS chunk_bytes
                   FROM recordings r ORDER BY r.created_at"""
            ).fetchall()
        return [dict(row) for row in rows]

    def _recording_row(self, recording_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """SELECT r.*,
                          (SELECT COUNT(*) FROM recording_chunks c WHERE c.recording_id=r.id)
                            AS chunk_count,
                          (SELECT COALESCE(SUM(c.byte_size), 0) FROM recording_chunks c
                           WHERE c.recording_id=r.id) AS chunk_bytes
                   FROM recordings r WHERE r.id=?""",
                (recording_id,),
            ).fetchone()
        return dict(row) if row else None

    def _qualify(
        self,
        row: dict[str, Any],
        retention: int | None,
        now: datetime,
        *,
        establish_grace: bool,
    ) -> dict[str, Any]:
        if int(row["chunk_count"] or 0) == 0:
            return {"eligible": False, "reason": "no_chunks"}
        if retention is None:
            return {"eligible": False, "reason": "permanent"}
        if row["status"] != "completed" or not row["resource_id"]:
            return {"eligible": False, "reason": "unfinished"}
        try:
            final_path, fingerprint = self._verify_final(row["resource_id"])
        except Exception:
            return {"eligible": False, "reason": "final_missing_or_corrupt"}
        verified_at = row.get("raw_chunks_verified_at")
        if not verified_at and establish_grace:
            verified_at = utc_now()
            with self.db.connect() as conn:
                conn.execute(
                    """UPDATE recordings SET raw_chunks_verified_at=?, updated_at=?
                       WHERE id=? AND raw_chunks_verified_at IS NULL""",
                    (verified_at, verified_at, row["id"]),
                )
        if not verified_at:
            return {"eligible": False, "reason": "awaiting_verified_grace", "final_size": fingerprint.st_size}
        cutoff = _instant(verified_at) + timedelta(days=retention)
        return {
            "eligible": now >= cutoff,
            "reason": "eligible" if now >= cutoff else "within_retention",
            "final_size": fingerprint.st_size,
            "final_path": final_path,
        }

    def _verify_final(self, resource_id: str) -> tuple[Path, os.stat_result]:
        with self.db.connect() as conn:
            resource = conn.execute(
                """SELECT storage_provider, storage_key, local_path
                   FROM resources WHERE id=? AND type='audio'""",
                (resource_id,),
            ).fetchone()
        if not resource or not resource["storage_key"]:
            raise ValueError("最终录音资源不存在")
        local_path = None
        if self.recordings.storage.name != "local" and resource["local_path"]:
            local_path = Path(resource["local_path"])
        target = self.recordings.storage.materialize(
            StoredObject(resource["storage_provider"], resource["storage_key"], local_path)
        )
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(target, flags)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
                raise ValueError("最终录音不是可读取的普通文件")
            self.recordings.assembler.verify(target)
            after = os.stat(target, follow_symlinks=False)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
            ):
                raise ValueError("最终录音在校验期间发生变化")
            return target, before
        finally:
            os.close(descriptor)

    def _delete_chunks(self, recording_id: str) -> int:
        if not self._SAFE_RECORDING_ID.fullmatch(recording_id):
            raise ValueError("录音标识格式无效")
        expected_dir = self.recordings.chunk_root / recording_id
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT sequence, byte_size, local_path FROM recording_chunks
                   WHERE recording_id=? ORDER BY sequence""",
                (recording_id,),
            ).fetchall()
        root_fd = os.open(
            self.recordings.chunk_root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            directory_fd = os.open(
                recording_id,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=root_fd,
            )
            try:
                names: list[tuple[str, int]] = []
                for row in rows:
                    raw = Path(row["local_path"])
                    expected = expected_dir / raw.name
                    if os.path.abspath(raw) != os.path.abspath(expected) or raw.name in {"", ".", ".."}:
                        raise ValueError("分片路径越界，已拒绝清理")
                    try:
                        descriptor = os.open(
                            raw.name,
                            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                            dir_fd=directory_fd,
                        )
                    except FileNotFoundError:
                        # A previous interrupted cleanup may already have removed this file.
                        continue
                    try:
                        info = os.fstat(descriptor)
                        if not stat.S_ISREG(info.st_mode):
                            raise ValueError("分片不是普通文件，已拒绝清理")
                        names.append((raw.name, info.st_size))
                    finally:
                        os.close(descriptor)
                deleted = 0
                for name, size in names:
                    try:
                        os.unlink(name, dir_fd=directory_fd)
                        deleted += size
                    except OSError as exc:
                        raise ChunkCleanupError(
                            "分片清理被中断，将在下次清理时继续恢复。",
                            deleted,
                        ) from exc
                return deleted
            finally:
                os.close(directory_fd)
        finally:
            os.close(root_fd)

    def _scan(
        self, directories: tuple[str, ...], *, include_root_files: bool = False
    ) -> tuple[int, int, bool]:
        size = count = 0
        truncated = False
        targets = [self.data_dir / item for item in directories]
        if include_root_files:
            targets.extend(item for item in self.data_dir.iterdir() if item.is_file() and not item.is_symlink())
        for target in targets:
            if target.is_symlink() or not target.exists():
                continue
            if target.is_file():
                size += target.stat().st_size
                count += 1
                continue
            for root, dirnames, filenames in os.walk(target, followlinks=False):
                dirnames[:] = [name for name in dirnames if not (Path(root) / name).is_symlink()]
                for name in filenames:
                    if count >= self._SCAN_LIMIT:
                        truncated = True
                        return size, count, truncated
                    path = Path(root) / name
                    try:
                        info = path.lstat()
                        if stat.S_ISREG(info.st_mode):
                            size += info.st_size
                            count += 1
                    except OSError:
                        continue
        return size, count, truncated

    @staticmethod
    def _category(label: str, size: int, count: int, *, key: str | None = None, truncated: bool = False) -> dict[str, Any]:
        return {"key": key or label, "label": label, "bytes": size, "files": count, "truncated": truncated}
