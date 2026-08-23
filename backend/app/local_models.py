from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import threading
import time
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from .database import Database, _decode
from .models import utc_now

MODEL_REPOSITORY_REVISION = "5359861c739e955e79d9a303bcbc70fb988958b1"
MODEL_REPOSITORY = "ggerganov/whisper.cpp"


@dataclass(frozen=True)
class LocalModelSpec:
    id: str
    name: str
    file_name: str
    sha256: str
    download_bytes: int
    disk_bytes: int
    speed: str
    accuracy: str
    languages: tuple[str, ...]
    use_case: str
    recommended: bool = False
    source_url: str = ""

    def public(self) -> dict[str, Any]:
        values = asdict(self)
        values["languages"] = list(self.languages)
        values.pop("source_url")
        return values


def _model_url(file_name: str) -> str:
    return (
        f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/"
        f"{MODEL_REPOSITORY_REVISION}/{file_name}?download=true"
    )


LOCAL_MODEL_CATALOG = (
    LocalModelSpec(
        id="small-q5_1",
        name="Whisper Small Q5",
        file_name="ggml-small-q5_1.bin",
        sha256="ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb",
        download_bytes=190_085_487,
        disk_bytes=190_085_487,
        speed="较快",
        accuracy="较好",
        languages=("中文", "英文", "多语言"),
        use_case="内存较小的 Mac、短课或先体验本地转写",
        source_url=_model_url("ggml-small-q5_1.bin"),
    ),
    LocalModelSpec(
        id="large-v3-turbo-q5_0",
        name="Whisper Large v3 Turbo Q5",
        file_name="ggml-large-v3-turbo-q5_0.bin",
        sha256="394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2",
        download_bytes=574_041_195,
        disk_bytes=574_041_195,
        speed="CPU 中等 / Metal 较快",
        accuracy="高（接近 Large v3）",
        languages=("中文", "英文", "多语言"),
        use_case="大学课堂与技术术语，低配机器优先兼顾准确率和速度",
        recommended=True,
        source_url=_model_url("ggml-large-v3-turbo-q5_0.bin"),
    ),
    LocalModelSpec(
        id="medium-q5_0",
        name="Whisper Medium Q5",
        file_name="ggml-medium-q5_0.bin",
        sha256="19fea4b380c3a618ec4723c3eef2eb785ffba0d0538cf43f8f235e7b3b34220f",
        download_bytes=539_212_467,
        disk_bytes=539_212_467,
        speed="中等",
        accuracy="高",
        languages=("中文", "英文", "多语言"),
        use_case="大学课堂与技术术语，准确率和体积更均衡",
        source_url=_model_url("ggml-medium-q5_0.bin"),
    ),
    LocalModelSpec(
        id="medium-q8_0",
        name="Whisper Medium Q8",
        file_name="ggml-medium-q8_0.bin",
        sha256="42a1ffcbe4167d224232443396968db4d02d4e8e87e213d3ee2e03095dea6502",
        download_bytes=823_369_779,
        disk_bytes=823_369_779,
        speed="较慢",
        accuracy="更高",
        languages=("中文", "英文", "多语言"),
        use_case="术语密集课程，优先识别质量且磁盘空间充足",
        source_url=_model_url("ggml-medium-q8_0.bin"),
    ),
)


class LocalModelError(RuntimeError):
    pass


class LocalModelConflict(LocalModelError):
    pass


class LocalModelNotInstalled(LocalModelError):
    pass


class ModelDownloadCancelled(LocalModelError):
    pass


class ModelDownloader(Protocol):
    def download(
        self,
        spec: LocalModelSpec,
        target: Path,
        offset: int,
        cancel_event: threading.Event,
        progress: Callable[[int], None],
    ) -> None: ...


class HttpModelDownloader:
    """HTTPS-only streaming downloader with safe Range fallback."""

    def __init__(
        self,
        *,
        chunk_bytes: int = 1024 * 1024,
        transport: httpx.BaseTransport | None = None,
    ):
        self.chunk_bytes = chunk_bytes
        self.transport = transport

    def download(
        self,
        spec: LocalModelSpec,
        target: Path,
        offset: int,
        cancel_event: threading.Event,
        progress: Callable[[int], None],
    ) -> None:
        if not spec.source_url.startswith("https://huggingface.co/"):
            raise LocalModelError("模型下载源不在允许的官方 HTTPS 域名中。")
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        timeout = httpx.Timeout(connect=20, read=60, write=30, pool=20)
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            transport=self.transport,
        ) as client:
            with client.stream("GET", spec.source_url, headers=headers) as response:
                if response.status_code == 416 and offset == spec.download_bytes:
                    progress(offset)
                    return
                if response.status_code not in {200, 206}:
                    raise LocalModelError(f"模型源返回 HTTP {response.status_code}。")
                append = bool(offset and response.status_code == 206)
                if append:
                    content_range = response.headers.get("content-range", "")
                    if not content_range.startswith(f"bytes {offset}-"):
                        raise LocalModelError("模型源返回了不匹配的断点范围，已停止写入。")
                else:
                    offset = 0
                    progress(0)
                written = offset
                with target.open("ab" if append else "wb") as output:
                    for chunk in response.iter_bytes(self.chunk_bytes):
                        if cancel_event.is_set():
                            raise ModelDownloadCancelled("下载已取消，已保留分片供下次继续。")
                        if not chunk:
                            continue
                        output.write(chunk)
                        written += len(chunk)
                        if written > spec.download_bytes:
                            raise LocalModelError("模型源返回的数据大于已审核大小，已停止下载。")
                        progress(written)
                    output.flush()
                    os.fsync(output.fileno())


class LocalModelManager:
    ACTIVE_STATUSES = {"queued", "downloading", "cancelling"}

    def __init__(
        self,
        db: Database,
        model_dir: Path,
        *,
        catalog: Iterable[LocalModelSpec] = LOCAL_MODEL_CATALOG,
        downloader: ModelDownloader | None = None,
        selected_model: Callable[[], str | None] | None = None,
        activate_model: Callable[[str], Any] | None = None,
    ):
        self.db = db
        self.model_dir = model_dir.expanduser().resolve()
        self.model_dir.mkdir(parents=True, exist_ok=True)
        (self.model_dir / ".locks").mkdir(exist_ok=True)
        self.catalog = {item.id: item for item in catalog}
        self.downloader = downloader or HttpModelDownloader()
        self.selected_model = selected_model or (lambda: None)
        self.activate_model = activate_model or (lambda _: None)
        self._guard = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}
        self._cancellations: dict[str, threading.Event] = {}
        self._stopping = False
        self._recover_interrupted()

    def _recover_interrupted(self) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """UPDATE local_model_downloads
                   SET status='paused', error=?, updated_at=?
                   WHERE status IN ('queued', 'downloading', 'cancelling')""",
                ("应用上次在下载期间退出，可继续下载。", utc_now()),
            )

    def _spec(self, model_id: str) -> LocalModelSpec:
        try:
            return self.catalog[model_id]
        except KeyError as exc:
            raise KeyError("local model") from exc

    def _row(self, model_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM local_model_downloads WHERE model_id=?", (model_id,)
            ).fetchone()
        return _decode(row)

    def _update(self, spec: LocalModelSpec, **changes: Any) -> dict[str, Any]:
        current = self._row(spec.id) or {}
        now = utc_now()
        values = {
            "model_id": spec.id,
            "status": current.get("status", "not_downloaded"),
            "file_name": spec.file_name,
            "expected_sha256": spec.sha256,
            "total_bytes": spec.download_bytes,
            "bytes_downloaded": current.get("bytes_downloaded", 0),
            "error": current.get("error"),
            "verified_size": current.get("verified_size"),
            "verified_mtime_ns": current.get("verified_mtime_ns"),
            "created_at": current.get("created_at", now),
            "updated_at": now,
            "completed_at": current.get("completed_at"),
        }
        values.update(changes)
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO local_model_downloads
                   (model_id, status, file_name, expected_sha256, total_bytes,
                    bytes_downloaded, error, verified_size, verified_mtime_ns,
                    created_at, updated_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(model_id) DO UPDATE SET
                     status=excluded.status, file_name=excluded.file_name,
                     expected_sha256=excluded.expected_sha256,
                     total_bytes=excluded.total_bytes,
                     bytes_downloaded=excluded.bytes_downloaded,
                     error=excluded.error, verified_size=excluded.verified_size,
                     verified_mtime_ns=excluded.verified_mtime_ns,
                     updated_at=excluded.updated_at, completed_at=excluded.completed_at""",
                tuple(values[key] for key in (
                    "model_id", "status", "file_name", "expected_sha256", "total_bytes",
                    "bytes_downloaded", "error", "verified_size", "verified_mtime_ns",
                    "created_at", "updated_at", "completed_at",
                )),
            )
        return values

    def _paths(self, spec: LocalModelSpec) -> tuple[Path, Path]:
        return self.model_dir / spec.file_name, self.model_dir / f".{spec.file_name}.part"

    @contextmanager
    def _file_lock(self, spec: LocalModelSpec, *, blocking: bool = False):
        lock_path = self.model_dir / ".locks" / f"{spec.id}.lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            try:
                fcntl.flock(descriptor, operation)
            except BlockingIOError as exc:
                raise LocalModelConflict("另一个 KnowledgeDebt 实例正在操作该模型。") from exc
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _inspect(self, spec: LocalModelSpec, *, force_verify: bool = False) -> dict[str, Any]:
        final_path, partial_path = self._paths(spec)
        row = self._row(spec.id) or {}
        selected = self.selected_model() == spec.file_name
        installed = False
        corrupted = False
        error = row.get("error")
        status = row.get("status", "not_downloaded")
        downloaded = partial_path.stat().st_size if partial_path.is_file() else 0

        if final_path.is_file():
            stat = final_path.stat()
            downloaded = stat.st_size
            if stat.st_size != spec.disk_bytes:
                corrupted = True
                error = "模型文件大小与官方清单不一致，可重新下载修复。"
            else:
                cached = (
                    not force_verify
                    and row.get("status") == "completed"
                    and row.get("expected_sha256") == spec.sha256
                    and row.get("verified_size") == stat.st_size
                    and row.get("verified_mtime_ns") == str(stat.st_mtime_ns)
                )
                if cached or self._sha256(final_path) == spec.sha256:
                    installed = True
                    status = "completed"
                    error = None
                    self._update(
                        spec,
                        status=status,
                        bytes_downloaded=stat.st_size,
                        error=None,
                        verified_size=stat.st_size,
                        verified_mtime_ns=str(stat.st_mtime_ns),
                        completed_at=row.get("completed_at") or utc_now(),
                    )
                else:
                    corrupted = True
                    error = "模型 SHA-256 校验失败，可重新下载修复。"
            if corrupted:
                status = "corrupted"
                self._update(
                    spec,
                    status=status,
                    bytes_downloaded=stat.st_size,
                    error=error,
                    verified_size=None,
                    verified_mtime_ns=None,
                )
        elif status == "completed":
            status = "missing"
            error = "模型文件已被移动或删除，可重新下载。"
            self._update(
                spec,
                status=status,
                bytes_downloaded=downloaded,
                error=error,
                verified_size=None,
                verified_mtime_ns=None,
                completed_at=None,
            )

        total = spec.download_bytes
        return {
            **spec.public(),
            "status": status,
            "installed": installed,
            "corrupted": corrupted,
            "selected": selected,
            "bytes_downloaded": downloaded,
            "progress": round(min(100.0, downloaded * 100 / total), 2) if total else 0.0,
            "can_resume": bool(0 < downloaded < total and not installed),
            "error": error,
            "model_directory": str(self.model_dir),
        }

    def list_models(self) -> list[dict[str, Any]]:
        return [self._inspect(spec) for spec in self.catalog.values()]

    def get_model(self, model_id: str) -> dict[str, Any]:
        return self._inspect(self._spec(model_id))

    def start_download(self, model_id: str, *, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise PermissionError("下载模型前需要确认下载大小和本地磁盘占用。")
        spec = self._spec(model_id)
        current = self._inspect(spec)
        if current["installed"] and not current["corrupted"]:
            return current
        with self._guard:
            existing = self._threads.get(model_id)
            if existing and existing.is_alive():
                return self._inspect(spec)
            _, partial_path = self._paths(spec)
            existing_bytes = partial_path.stat().st_size if partial_path.is_file() else 0
            remaining = max(0, spec.download_bytes - existing_bytes)
            free = shutil.disk_usage(self.model_dir).free
            if free < remaining + 64 * 1024 * 1024:
                raise LocalModelError(
                    f"磁盘空间不足：还需约 {remaining / 1_048_576:.0f} MB，"
                    "并至少保留 64 MB 安全空间。"
                )
            cancellation = threading.Event()
            self._cancellations[model_id] = cancellation
            self._update(
                spec,
                status="queued",
                bytes_downloaded=existing_bytes,
                error=None,
                verified_size=None,
                verified_mtime_ns=None,
                completed_at=None,
            )
            thread = threading.Thread(
                target=self._download_worker,
                args=(spec, cancellation),
                name=f"local-model-{spec.id}",
                daemon=True,
            )
            self._threads[model_id] = thread
            thread.start()
        return self._inspect(spec)

    def _download_worker(self, spec: LocalModelSpec, cancellation: threading.Event) -> None:
        final_path, partial_path = self._paths(spec)
        last_persisted = 0.0

        def progress(downloaded: int) -> None:
            nonlocal last_persisted
            now = time.monotonic()
            if now - last_persisted >= 0.25 or downloaded in {0, spec.download_bytes}:
                self._update(
                    spec,
                    status="downloading",
                    bytes_downloaded=downloaded,
                    error=None,
                )
                last_persisted = now

        try:
            with self._file_lock(spec):
                offset = partial_path.stat().st_size if partial_path.is_file() else 0
                if offset > spec.download_bytes:
                    with partial_path.open("wb"):
                        pass
                    offset = 0
                progress(offset)
                self.downloader.download(spec, partial_path, offset, cancellation, progress)
                if cancellation.is_set():
                    raise ModelDownloadCancelled("下载已取消，已保留分片供下次继续。")
                actual_size = partial_path.stat().st_size if partial_path.is_file() else 0
                if actual_size != spec.download_bytes:
                    raise LocalModelError(
                        f"下载不完整：应为 {spec.download_bytes} 字节，实际为 {actual_size} 字节。"
                    )
                if self._sha256(partial_path) != spec.sha256:
                    raise LocalModelError("模型 SHA-256 校验失败，分片已保留以便重新下载。")
                os.chmod(partial_path, 0o600)
                os.replace(partial_path, final_path)
                directory = os.open(self.model_dir, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
                stat = final_path.stat()
                self._update(
                    spec,
                    status="completed",
                    bytes_downloaded=stat.st_size,
                    error=None,
                    verified_size=stat.st_size,
                    verified_mtime_ns=str(stat.st_mtime_ns),
                    completed_at=utc_now(),
                )
        except ModelDownloadCancelled as exc:
            size = partial_path.stat().st_size if partial_path.is_file() else 0
            self._update(
                spec,
                status="paused" if self._stopping else "cancelled",
                bytes_downloaded=size,
                error=("应用退出前已暂停，可继续下载。" if self._stopping else str(exc)),
            )
        except LocalModelConflict as exc:
            self._update(spec, status="paused", error=str(exc))
        except Exception as exc:
            size = partial_path.stat().st_size if partial_path.is_file() else 0
            if isinstance(exc, LocalModelError):
                message = str(exc)
            elif isinstance(exc, httpx.HTTPError):
                message = f"下载连接失败（{type(exc).__name__}），请检查网络后继续下载。"
            else:
                message = f"模型下载失败（{type(exc).__name__}），可保留分片后重试。"
            self._update(spec, status="failed", bytes_downloaded=size, error=message[:500])
        finally:
            with self._guard:
                self._threads.pop(spec.id, None)
                self._cancellations.pop(spec.id, None)

    def cancel_download(self, model_id: str) -> dict[str, Any]:
        spec = self._spec(model_id)
        with self._guard:
            cancellation = self._cancellations.get(model_id)
            if cancellation:
                self._update(spec, status="cancelling", error="正在安全停止下载…")
                # Persist the transitional state before waking the worker. Otherwise the
                # worker can store ``cancelled`` first and this method can overwrite that
                # terminal state with ``cancelling`` after the thread has already exited.
                cancellation.set()
            else:
                row = self._row(model_id) or {}
                if row.get("status") in self.ACTIVE_STATUSES:
                    self._update(
                        spec,
                        status="paused",
                        error="下载进程已不存在，可继续下载。",
                    )
        return self._inspect(spec)

    def activate(self, model_id: str) -> dict[str, Any]:
        spec = self._spec(model_id)
        with self._file_lock(spec):
            state = self._inspect(spec)
            if not state["installed"] or state["corrupted"]:
                raise LocalModelNotInstalled("模型尚未完成校验，不能切换使用。")
            self.activate_model(spec.file_name)
        return self._inspect(spec)

    def delete(self, model_id: str, *, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise PermissionError("删除本地模型前需要明确确认。")
        spec = self._spec(model_id)
        if self.selected_model() == spec.file_name:
            raise LocalModelConflict("该模型正在作为默认本地转写模型使用，请先切换到其他模型或 Provider。")
        with self._guard:
            thread = self._threads.get(model_id)
            if thread and thread.is_alive():
                raise LocalModelConflict("模型仍在下载，请先取消并等待下载停止。")
        with self._file_lock(spec):
            final_path, partial_path = self._paths(spec)
            final_path.unlink(missing_ok=True)
            partial_path.unlink(missing_ok=True)
            self._update(
                spec,
                status="not_downloaded",
                bytes_downloaded=0,
                error=None,
                verified_size=None,
                verified_mtime_ns=None,
                completed_at=None,
            )
        return self._inspect(spec)

    def wait(self, model_id: str, timeout: float = 10) -> None:
        with self._guard:
            thread = self._threads.get(model_id)
        if thread:
            thread.join(timeout)

    def shutdown(self) -> None:
        self._stopping = True
        with self._guard:
            items = list(self._cancellations.items())
        for model_id, cancellation in items:
            spec = self._spec(model_id)
            self._update(spec, status="paused", error="应用退出前已暂停，可继续下载。")
            cancellation.set()
        for model_id, _ in items:
            self.wait(model_id, timeout=2)
