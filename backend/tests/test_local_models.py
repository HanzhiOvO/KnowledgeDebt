from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.automation import AutomationRepository
from app.config import Settings
from app.database import Database
from app.local_models import (
    HttpModelDownloader,
    LocalModelConflict,
    LocalModelManager,
    LocalModelSpec,
    ModelDownloadCancelled,
)
from app.main import create_app
from app.provider_registry import ProviderRegistry
from app.secrets import SecretStore


class BytesDownloader:
    def __init__(self, content: bytes, *, block_once: bool = False):
        self.content = content
        self.block_once = block_once
        self.started = threading.Event()
        self.offsets: list[int] = []

    def download(self, spec, target, offset, cancel_event, progress) -> None:
        self.offsets.append(offset)
        mode = "ab" if offset else "wb"
        halfway = len(self.content) // 2
        with target.open(mode) as output:
            position = offset
            while position < len(self.content):
                end = min(len(self.content), position + 4096)
                output.write(self.content[position:end])
                output.flush()
                position = end
                progress(position)
                if self.block_once and position >= halfway:
                    self.started.set()
                    while not cancel_event.wait(0.01):
                        pass
                    self.block_once = False
                    raise ModelDownloadCancelled("cancelled by test")
                if cancel_event.is_set():
                    raise ModelDownloadCancelled("cancelled by test")


def tiny_spec(content: bytes) -> LocalModelSpec:
    return LocalModelSpec(
        id="tiny-test",
        name="Tiny Test",
        file_name="ggml-tiny-test.bin",
        sha256=hashlib.sha256(content).hexdigest(),
        download_bytes=len(content),
        disk_bytes=len(content),
        speed="快",
        accuracy="测试",
        languages=("中文",),
        use_case="离线回归测试",
        recommended=True,
        source_url="https://huggingface.co/test/repository/resolve/revision/model.bin",
    )


def wait_for_status(manager: LocalModelManager, status: str, timeout: float = 5) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = manager.get_model("tiny-test")
        if state["status"] == status:
            return state
        time.sleep(0.01)
    raise AssertionError(f"model did not reach {status}: {manager.get_model('tiny-test')}")


def test_download_requires_confirmation_and_completes_with_sha256(tmp_path: Path):
    content = b"verified-local-model" * 4096
    spec = tiny_spec(content)
    downloader = BytesDownloader(content)
    manager = LocalModelManager(
        Database(tmp_path / "models.sqlite3"),
        tmp_path / "models",
        catalog=(spec,),
        downloader=downloader,
    )

    with pytest.raises(PermissionError, match="确认"):
        manager.start_download(spec.id, confirmed=False)
    queued = manager.start_download(spec.id, confirmed=True)
    assert queued["status"] in {"queued", "downloading", "completed"}
    manager.wait(spec.id)
    completed = manager.get_model(spec.id)

    assert completed["status"] == "completed"
    assert completed["installed"] is True
    assert completed["progress"] == 100
    assert completed["bytes_downloaded"] == len(content)
    assert (tmp_path / "models" / spec.file_name).read_bytes() == content
    assert not (tmp_path / "models" / f".{spec.file_name}.part").exists()
    assert downloader.offsets == [0]


def test_http_downloader_resumes_with_range_and_safely_restarts_if_ignored(tmp_path: Path):
    content = b"official-model-bytes" * 1024
    spec = tiny_spec(content)
    offset = len(content) // 3
    seen_ranges: list[str | None] = []

    def range_handler(request: httpx.Request) -> httpx.Response:
        seen_ranges.append(request.headers.get("range"))
        return httpx.Response(
            206,
            headers={"Content-Range": f"bytes {offset}-{len(content) - 1}/{len(content)}"},
            content=content[offset:],
        )

    resumed = tmp_path / "resumed.part"
    resumed.write_bytes(content[:offset])
    HttpModelDownloader(
        chunk_bytes=1024,
        transport=httpx.MockTransport(range_handler),
    ).download(spec, resumed, offset, threading.Event(), lambda _: None)
    assert seen_ranges == [f"bytes={offset}-"]
    assert resumed.read_bytes() == content

    def no_range_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("range") == f"bytes={offset}-"
        return httpx.Response(200, content=content)

    restarted = tmp_path / "restarted.part"
    restarted.write_bytes(content[:offset])
    progress: list[int] = []
    HttpModelDownloader(
        chunk_bytes=1024,
        transport=httpx.MockTransport(no_range_handler),
    ).download(spec, restarted, offset, threading.Event(), progress.append)
    assert progress[0] == 0
    assert restarted.read_bytes() == content


def test_cancel_retains_partial_and_restart_resumes_from_exact_offset(tmp_path: Path):
    content = os.urandom(128 * 1024)
    spec = tiny_spec(content)
    database = Database(tmp_path / "resume.sqlite3")
    first_downloader = BytesDownloader(content, block_once=True)
    first = LocalModelManager(
        database,
        tmp_path / "models",
        catalog=(spec,),
        downloader=first_downloader,
    )

    first.start_download(spec.id, confirmed=True)
    assert first_downloader.started.wait(2)
    cancelling = first.cancel_download(spec.id)
    assert cancelling["status"] in {"cancelling", "cancelled"}
    first.wait(spec.id)
    cancelled = first.get_model(spec.id)
    partial_bytes = cancelled["bytes_downloaded"]
    assert cancelled["status"] == "cancelled"
    assert 0 < partial_bytes < len(content)

    resumed_downloader = BytesDownloader(content)
    restarted = LocalModelManager(
        database,
        tmp_path / "models",
        catalog=(spec,),
        downloader=resumed_downloader,
    )
    assert restarted.get_model(spec.id)["can_resume"] is True
    restarted.start_download(spec.id, confirmed=True)
    restarted.wait(spec.id)

    assert resumed_downloader.offsets == [partial_bytes]
    assert restarted.get_model(spec.id)["status"] == "completed"
    assert (tmp_path / "models" / spec.file_name).read_bytes() == content


def test_corruption_is_detected_redownloaded_and_selected_model_cannot_be_deleted(tmp_path: Path):
    content = b"course-model-weights" * 4096
    spec = tiny_spec(content)
    selected: list[str | None] = [None]

    def activate(file_name: str) -> None:
        selected[0] = file_name

    manager = LocalModelManager(
        Database(tmp_path / "corruption.sqlite3"),
        tmp_path / "models",
        catalog=(spec,),
        downloader=BytesDownloader(content),
        selected_model=lambda: selected[0],
        activate_model=activate,
    )
    manager.start_download(spec.id, confirmed=True)
    manager.wait(spec.id)
    model_path = tmp_path / "models" / spec.file_name
    stat = model_path.stat()
    model_path.write_bytes(b"X" + content[1:])
    os.utime(model_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    corrupted = manager.get_model(spec.id)
    assert corrupted["status"] == "corrupted"
    assert corrupted["corrupted"] is True
    assert "SHA-256" in corrupted["error"]

    manager.start_download(spec.id, confirmed=True)
    manager.wait(spec.id)
    assert manager.get_model(spec.id)["installed"] is True
    activated = manager.activate(spec.id)
    assert activated["selected"] is True
    with pytest.raises(LocalModelConflict, match="正在作为默认"):
        manager.delete(spec.id, confirmed=True)

    selected[0] = None
    deleted = manager.delete(spec.id, confirmed=True)
    assert deleted["status"] == "not_downloaded"
    assert deleted["bytes_downloaded"] == 0
    assert not model_path.exists()


def test_local_model_api_exposes_progress_cancel_activate_and_delete_guards(tmp_path: Path):
    content = b"api-model" * 8192
    spec = tiny_spec(content)
    selected: list[str | None] = [None]
    database = Database(tmp_path / "api.sqlite3")
    manager = LocalModelManager(
        database,
        tmp_path / "models",
        catalog=(spec,),
        downloader=BytesDownloader(content),
        selected_model=lambda: selected[0],
        activate_model=lambda value: selected.__setitem__(0, value),
    )
    settings = Settings(
        data_dir=tmp_path / "data",
        ai_provider="openai_compatible",
        asr_provider="openai_compatible",
        api_key=None,
        base_url="https://api.openai.com/v1",
        ai_model="test-ai",
        asr_model="test-asr",
        local_asr_model_dir=tmp_path / "models",
    )

    with TestClient(
        create_app(settings=settings, db=database, local_model_manager=manager)
    ) as client:
        listed = client.get("/settings/local-models")
        assert listed.status_code == 200
        assert listed.json()[0]["download_bytes"] == len(content)
        assert client.post(
            f"/settings/local-models/{spec.id}/download", json={"confirmed": False}
        ).status_code == 409

        response = client.post(
            f"/settings/local-models/{spec.id}/download", json={"confirmed": True}
        )
        assert response.status_code == 202
        manager.wait(spec.id)
        assert client.get("/settings/local-models").json()[0]["status"] == "completed"
        assert client.post(f"/settings/local-models/{spec.id}/activate").status_code == 200
        assert client.request(
            "DELETE",
            f"/settings/local-models/{spec.id}",
            json={"confirmed": True},
        ).status_code == 409

        selected[0] = None
        deleted = client.request(
            "DELETE",
            f"/settings/local-models/{spec.id}",
            json={"confirmed": True},
        )
        assert deleted.status_code == 200
        assert deleted.json()["status"] == "not_downloaded"


def test_activating_verified_model_updates_real_local_provider_route(tmp_path: Path):
    content = b"provider-model" * 4096
    spec = tiny_spec(content)
    binary = tmp_path / "whisper-cli"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    database = Database(tmp_path / "provider.sqlite3")
    repository = AutomationRepository(database)
    settings = Settings(
        data_dir=tmp_path / "data",
        ai_provider="openai_compatible",
        asr_provider="openai_compatible",
        api_key=None,
        base_url="https://api.openai.com/v1",
        ai_model="test-ai",
        asr_model="test-asr",
        local_asr_binary=str(binary),
        local_asr_model_dir=tmp_path / "models",
    )
    registry = ProviderRegistry(repository, SecretStore(None), settings)
    registry.ensure_environment_profiles()
    manager = LocalModelManager(
        database,
        tmp_path / "models",
        catalog=(spec,),
        downloader=BytesDownloader(content),
        selected_model=registry.active_local_model_filename,
        activate_model=registry.activate_local_model,
    )

    manager.start_download(spec.id, confirmed=True)
    manager.wait(spec.id)
    manager.activate(spec.id)

    active = repository.get_provider_defaults()["asr"]
    assert active["adapter"] == "local_whisper_cpp"
    assert active["default_model"] == spec.file_name
    status = registry.local_asr_status()
    assert status["ready"] is True
    assert status["active"] is True
    assert Path(status["model_resolved"]) == tmp_path / "models" / spec.file_name
