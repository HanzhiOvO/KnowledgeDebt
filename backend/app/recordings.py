from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .automation import AutomationRepository
from .database import Database, _decode
from .models import utc_now
from .recording_media import BrowserRecordingAssembler, RecordingAssembler
from .storage import StorageProvider


class RecordingConflict(ValueError):
    """同一录音分片序号携带了不同内容。"""


class RecordingIncomplete(ValueError):
    def __init__(self, missing_sequences: list[int]):
        super().__init__("录音分片不完整")
        self.missing_sequences = missing_sequences


class RecordingStorageError(OSError):
    """A raw chunk could not be durably persisted."""


class RecordingManager:
    """Durable browser-recording boundary.

    Raw chunks are retained after finalization. A later maintenance policy may remove them,
    but only after the final resource has been materialized and verified.
    """

    MAX_CHUNK_BYTES = 20 * 1024 * 1024

    def __init__(
        self,
        db: Database,
        automation: AutomationRepository,
        storage: StorageProvider,
        chunk_root: Path,
        *,
        ffmpeg_path: str = "ffmpeg",
        assembler: RecordingAssembler | None = None,
    ):
        self.db = db
        self.automation = automation
        self.storage = storage
        self.chunk_root = chunk_root.resolve()
        self.chunk_root.mkdir(parents=True, exist_ok=True)
        self.assembler = assembler or BrowserRecordingAssembler(ffmpeg_path)

    def create(self, session_id: str, values: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        self.db.get_session(session_id)
        now = utc_now()
        recording_id = values["recording_id"]
        with self.db.connect(immediate=True) as conn:
            inserted = conn.execute(
                """INSERT INTO recordings
                   (id, session_id, mime_type, filename, start_offset, session_duration,
                    auto_transcribe, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO NOTHING RETURNING id""",
                (
                    recording_id,
                    session_id,
                    values["mime_type"],
                    values["filename"],
                    values.get("start_offset", 0),
                    values.get("session_duration"),
                    int(values.get("auto_transcribe", True)),
                    now,
                    now,
                ),
            ).fetchone()
            row = conn.execute("SELECT * FROM recordings WHERE id=?", (recording_id,)).fetchone()
            if not row:
                raise RuntimeError("录音任务创建失败")
            if row["session_id"] != session_id:
                raise RecordingConflict("recording_id 已属于另一个 Session")
            if row["mime_type"] != values["mime_type"]:
                raise RecordingConflict("recording_id 的媒体类型与首次创建不一致")
            immutable = {
                "filename": values["filename"],
                "start_offset": values.get("start_offset", 0),
                "session_duration": values.get("session_duration"),
                "auto_transcribe": int(values.get("auto_transcribe", True)),
            }
            for key, expected in immutable.items():
                if row[key] != expected:
                    raise RecordingConflict(f"recording_id 的 {key} 与首次创建不一致")
        return self.get(recording_id), inserted is not None

    def get(self, recording_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM recordings WHERE id=?", (recording_id,)).fetchone()
            chunks = conn.execute(
                """SELECT sequence, checksum, byte_size, mime_type, stream_id, stream_index,
                          received_at
                   FROM recording_chunks WHERE recording_id=? ORDER BY sequence""",
                (recording_id,),
            ).fetchall()
        if not row:
            raise KeyError("recording")
        result = _decode(row)  # type: ignore[assignment]
        result["chunks"] = [_decode(item) for item in chunks]
        received = [item["sequence"] for item in chunks]
        result["received_sequences"] = received
        result["next_sequence"] = max(received, default=-1) + 1
        result["next_stream_index"] = max(
            (int(item["stream_index"]) for item in chunks), default=-1
        ) + 1
        result["missing_sequences"] = (
            [value for value in range(row["last_sequence"] + 1) if value not in set(received)]
            if row["last_sequence"] is not None
            else []
        )
        return result

    def list_incomplete(self, session_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT id FROM recordings WHERE status NOT IN ('completed', 'abandoned')"
        args: tuple[Any, ...] = ()
        if session_id:
            query += " AND session_id=?"
            args = (session_id,)
        query += " ORDER BY updated_at DESC"
        with self.db.connect() as conn:
            rows = conn.execute(query, args).fetchall()
        return [self.get(row["id"]) for row in rows]

    def save_chunk(
        self,
        recording_id: str,
        sequence: int,
        content: bytes,
        mime_type: str,
        supplied_checksum: str | None,
        stream_id: str = "legacy",
        stream_index: int = 0,
    ) -> dict[str, Any]:
        if sequence < 0 or sequence > 100_000:
            raise ValueError("分片序号必须在 0 到 100000 之间")
        if stream_index < 0 or stream_index > 10_000:
            raise ValueError("录音流序号必须在 0 到 10000 之间")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", stream_id):
            raise ValueError("录音流标识格式无效")
        if not content:
            raise ValueError("录音分片不能为空")
        if len(content) > self.MAX_CHUNK_BYTES:
            raise ValueError("单个录音分片不能超过 20 MB")
        checksum = hashlib.sha256(content).hexdigest()
        if supplied_checksum and not re.fullmatch(r"[0-9a-fA-F]{64}", supplied_checksum):
            raise ValueError("X-Chunk-SHA256 必须是 SHA-256 十六进制摘要")
        if supplied_checksum and supplied_checksum.lower() != checksum:
            raise RecordingConflict("录音分片校验失败，请重新上传该分片")

        final_path = self._chunk_path(recording_id, sequence, checksum)
        with self._recording_lock(recording_id):
            with self.db.connect(immediate=True) as conn:
                conn.execute(
                    "UPDATE recordings SET updated_at=updated_at WHERE id=?", (recording_id,)
                )
                recording = conn.execute(
                    "SELECT * FROM recordings WHERE id=?", (recording_id,)
                ).fetchone()
                if not recording:
                    raise KeyError("recording")
                if recording["status"] in {"completed", "abandoned", "finalizing"}:
                    raise RecordingConflict("已完成、正在保存或已放弃的录音不能再接收分片")
                try:
                    self._atomic_write(final_path, content, replace=False)
                except FileExistsError:
                    if not self._path_matches(final_path, len(content), checksum):
                        raise RecordingStorageError(
                            "录音分片存储发生校验冲突；已停止写入，请检查磁盘。"
                        ) from None
                except OSError as exc:
                    raise RecordingStorageError(
                        "无法写入录音分片，可能是磁盘空间不足；录音已停止继续保存。"
                    ) from exc
                inserted = conn.execute(
                    """INSERT INTO recording_chunks
                       (recording_id, sequence, checksum, byte_size, mime_type, local_path,
                        stream_id, stream_index, received_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(recording_id, sequence) DO NOTHING RETURNING sequence""",
                    (
                        recording_id,
                        sequence,
                        checksum,
                        len(content),
                        mime_type,
                        str(final_path),
                        stream_id,
                        stream_index,
                        utc_now(),
                    ),
                ).fetchone()
                existing = conn.execute(
                    "SELECT * FROM recording_chunks WHERE recording_id=? AND sequence=?",
                    (recording_id, sequence),
                ).fetchone()
                if not existing:
                    raise RuntimeError("录音分片元数据保存失败")
                expected = {
                    "checksum": checksum,
                    "byte_size": len(content),
                    "mime_type": mime_type,
                    "stream_id": stream_id,
                    "stream_index": stream_index,
                }
                if any(existing[key] != value for key, value in expected.items()):
                    if inserted is None and existing["checksum"] != checksum:
                        final_path.unlink(missing_ok=True)
                    raise RecordingConflict("同一分片序号已经保存了不同内容或录音流信息")
                existing_path = Path(existing["local_path"])
                if not self._path_matches(existing_path, len(content), checksum):
                    self._atomic_write(final_path, content, replace=True)
                    conn.execute(
                        "UPDATE recording_chunks SET local_path=? WHERE recording_id=? AND sequence=?",
                        (str(final_path), recording_id, sequence),
                    )
                conn.execute(
                    "UPDATE recordings SET status='recording', failure_reason=NULL, updated_at=? WHERE id=?",
                    (utc_now(), recording_id),
                )
        return {
            "recording_id": recording_id,
            "sequence": sequence,
            "checksum": checksum,
            "byte_size": len(content),
            "stream_id": stream_id,
            "stream_index": stream_index,
            "deduplicated": inserted is None,
        }

    def finalize(
        self,
        recording_id: str,
        last_sequence: int,
        duration_seconds: float,
    ) -> tuple[dict[str, Any], bool]:
        if last_sequence < 0 or last_sequence > 100_000:
            raise ValueError("末分片序号必须在 0 到 100000 之间")
        with self._recording_lock(recording_id):
            resource, created, auto_transcribe = self._finalize_locked(
                recording_id, last_sequence, duration_seconds
            )
        self.automation.ensure_resource_automation(
            resource["id"], state="saved", auto_transcribe=auto_transcribe
        )
        return resource, created

    def _finalize_locked(
        self,
        recording_id: str,
        last_sequence: int,
        duration_seconds: float,
    ) -> tuple[dict[str, Any], bool, bool]:
        with self.db.connect(immediate=True) as conn:
            conn.execute("UPDATE recordings SET updated_at=updated_at WHERE id=?", (recording_id,))
            recording = conn.execute(
                "SELECT * FROM recordings WHERE id=?", (recording_id,)
            ).fetchone()
            if not recording:
                raise KeyError("recording")
            if recording["status"] == "abandoned":
                raise RecordingConflict("已放弃的录音不能保存")
            if recording["resource_id"]:
                return (
                    self.db.get_resource(recording["resource_id"]),
                    False,
                    bool(recording["auto_transcribe"]),
                )
            raw_chunks = conn.execute(
                "SELECT * FROM recording_chunks WHERE recording_id=? ORDER BY sequence",
                (recording_id,),
            ).fetchall()
            chunks = [dict(item) for item in raw_chunks]
            received = {int(item["sequence"]) for item in chunks}
            missing = [value for value in range(last_sequence + 1) if value not in received]
            if missing:
                raise RecordingIncomplete(missing)
            beyond_last = sorted(value for value in received if value > last_sequence)
            if beyond_last:
                raise RecordingConflict(
                    "末分片序号早于后端已经收到的分片，请刷新录音状态后重试。"
                )
            conn.execute(
                """UPDATE recordings SET status='finalizing', last_sequence=?, duration_seconds=?,
                   failure_reason=NULL, updated_at=? WHERE id=?""",
                (last_sequence, duration_seconds, utc_now(), recording_id),
            )

        assembled = None
        try:
            assembled = self.assembler.assemble(recording_id, chunks)
            final_name = f"{Path(recording['filename']).stem or 'browser-recording'}.flac"
            key = (
                f"{recording['session_id']}/recordings/{recording_id}_"
                f"{self._safe_name(final_name)}"
            )
            with assembled.path.open("rb") as handle:
                stored = self.storage.save(key, handle, assembled.mime_type)
            target = self.storage.materialize(stored)
            if not target.is_file() or target.stat().st_size <= 0:
                raise RecordingStorageError("最终录音文件不可读取或为空；原始分片已保留。")
            measured_duration = self.assembler.verify(target)
        except Exception as exc:
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE recordings SET status='failed', failure_reason=?, updated_at=? WHERE id=?",
                    (str(exc)[:1000], utc_now(), recording_id),
                )
            raise
        finally:
            if assembled:
                assembled.path.unlink(missing_ok=True)

        now = utc_now()
        created = False
        with self.db.connect(immediate=True) as conn:
            conn.execute("UPDATE recordings SET updated_at=updated_at WHERE id=?", (recording_id,))
            current = conn.execute("SELECT * FROM recordings WHERE id=?", (recording_id,)).fetchone()
            if current["resource_id"]:
                resource_id = current["resource_id"]
            else:
                resource_id = str(uuid.uuid4())
                end_offset = current["start_offset"] + measured_duration
                conn.execute(
                    """INSERT INTO resources
                       (id, session_id, type, evidence_level, name, mime_type, local_path,
                        storage_provider, storage_key, duration_seconds, start_offset, end_offset,
                        session_duration, capture_range_json, upload_state, created_at, updated_at)
                       VALUES (?, ?, 'audio', 'classroom', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               'local_only', ?, ?)""",
                    (
                        resource_id,
                        current["session_id"],
                        final_name,
                        assembled.mime_type,
                        str(target) if stored.local_path else None,
                        stored.provider,
                        stored.key,
                        measured_duration,
                        current["start_offset"],
                        end_offset,
                        current["session_duration"],
                        json.dumps([current["start_offset"], end_offset]),
                        now,
                        now,
                    ),
                )
                conn.execute(
                    """UPDATE recordings SET status='completed', resource_id=?, completed_at=?,
                       duration_seconds=?, failure_reason=NULL, raw_chunks_verified_at=?,
                       updated_at=? WHERE id=?""",
                    (resource_id, now, measured_duration, now, now, recording_id),
                )
                created = True
        return self.db.get_resource(resource_id), created, bool(recording["auto_transcribe"])

    def abandon(self, recording_id: str) -> dict[str, Any]:
        with self._recording_lock(recording_id):
            with self.db.connect(immediate=True) as conn:
                row = conn.execute("SELECT * FROM recordings WHERE id=?", (recording_id,)).fetchone()
                if not row:
                    raise KeyError("recording")
                if row["resource_id"]:
                    raise RecordingConflict("已保存为课堂资源的录音不能放弃")
                conn.execute(
                    "UPDATE recordings SET status='abandoned', updated_at=? WHERE id=?",
                    (utc_now(), recording_id),
                )
        return self.get(recording_id)

    def _chunk_path(self, recording_id: str, sequence: int, checksum: str = "legacy") -> Path:
        target = (
            self.chunk_root / recording_id / f"{sequence:08d}-{checksum[:16]}.chunk"
        ).resolve()
        if self.chunk_root not in target.parents:
            raise ValueError("录音分片路径越界")
        return target

    @staticmethod
    def _atomic_write(target: Path, content: bytes, *, replace: bool) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if replace:
                os.replace(temporary, target)
            else:
                os.link(temporary, target)
                temporary.unlink()
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _path_matches(path: Path, byte_size: int, checksum: str) -> bool:
        try:
            if not path.is_file() or path.stat().st_size != byte_size:
                return False
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                while block := handle.read(1024 * 1024):
                    digest.update(block)
            return digest.hexdigest() == checksum
        except OSError:
            return False

    @contextmanager
    def _recording_lock(self, recording_id: str) -> Iterator[None]:
        lock_path = self._chunk_path(recording_id, 0).parent / ".recording.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _safe_name(name: str) -> str:
        clean = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]", "_", Path(name).name)
        return clean[:180] or "browser-recording.webm"
