from __future__ import annotations

import json
import stat
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.automation import AutomationRepository
from app.config import Settings
from app.database import Database
from app.diagnostics import REDACTED, DiagnosticExporter, redact_structure, redact_text
from app.main import create_app
from app.maintenance import StorageMaintenance
from app.models import CourseCreate, SessionCreate
from app.recording_media import AssembledRecording
from app.recordings import RecordingManager
from app.storage import LocalStorageProvider


class StrictAssembler:
    def assemble(self, recording_id: str, chunks: list[dict]) -> AssembledRecording:
        target = Path(chunks[0]["local_path"]).parent / f".{recording_id}.test.flac"
        target.write_bytes(b"valid-audio:" + b"".join(Path(item["local_path"]).read_bytes() for item in chunks))
        return AssembledRecording(target, 5)

    def verify(self, path: Path) -> float:
        if not path.read_bytes().startswith(b"valid-audio:"):
            raise ValueError("corrupt final audio")
        return 5


def build_maintenance(tmp_path: Path) -> tuple[Database, AutomationRepository, RecordingManager, StorageMaintenance, dict]:
    data_dir = tmp_path / "data"
    database = Database(data_dir / "test.sqlite3")
    automation = AutomationRepository(database)
    course = database.create_course(CourseCreate(name="测试课程", semester="2026"))
    session = database.create_session(course["id"], SessionCreate(title="测试课堂"))
    manager = RecordingManager(
        database,
        automation,
        LocalStorageProvider(data_dir / "resources"),
        data_dir / "recording-chunks",
        assembler=StrictAssembler(),
    )
    return database, automation, manager, StorageMaintenance(database, automation, manager, data_dir), session


def complete_recording(manager: RecordingManager, session: dict, recording_id: str, body: bytes = b"chunk") -> tuple[dict, Path]:
    recording, _ = manager.create(
        session["id"],
        {
            "recording_id": recording_id,
            "mime_type": "audio/webm",
            "filename": "lesson.webm",
            "auto_transcribe": False,
        },
    )
    manager.save_chunk(recording["id"], 0, body, "audio/webm", None)
    resource, _ = manager.finalize(recording["id"], 0, 5)
    return resource, next((manager.chunk_root / recording_id).glob("*.chunk"))


def age_verification(database: Database, recording_id: str, days: float) -> None:
    value = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with database.connect() as conn:
        conn.execute(
            "UPDATE recordings SET raw_chunks_verified_at=? WHERE id=?",
            (value, recording_id),
        )


@pytest.mark.parametrize(
    ("retention", "age", "eligible"),
    [(7, 6.99, False), (7, 7.01, True), (14, 13.99, False), (14, 14.01, True), (30, 30.01, True), (None, 365, False)],
)
def test_cleanup_retention_boundaries_and_permanent(tmp_path: Path, retention: int | None, age: float, eligible: bool):
    database, automation, manager, maintenance, session = build_maintenance(tmp_path)
    complete_recording(manager, session, "recording-boundary")
    automation.update_app_settings({"recording_chunk_retention_days": retention})
    age_verification(database, "recording-boundary", age)

    preview = maintenance.preview()

    assert (preview["recording_count"] == 1) is eligible


def test_existing_recording_gets_full_grace_and_missing_or_corrupt_final_is_protected(tmp_path: Path):
    database, _, manager, maintenance, session = build_maintenance(tmp_path)
    first_resource, first_chunk = complete_recording(manager, session, "recording-existing")
    missing_resource, missing_chunk = complete_recording(manager, session, "recording-missing")
    corrupt_resource, corrupt_chunk = complete_recording(manager, session, "recording-corrupt")
    with database.connect() as conn:
        conn.execute("UPDATE recordings SET raw_chunks_verified_at=NULL WHERE id=?", ("recording-existing",))
    Path(missing_resource["local_path"]).unlink()
    Path(corrupt_resource["local_path"]).write_bytes(b"not-audio")
    age_verification(database, "recording-missing", 40)
    age_verification(database, "recording-corrupt", 40)

    snapshot = maintenance.refresh()
    preview = maintenance.preview()

    assert preview["recording_count"] == 0
    assert snapshot["cleanup"]["reasons"]["within_retention"] == 1
    assert snapshot["cleanup"]["reasons"]["final_missing_or_corrupt"] == 2
    assert first_chunk.exists() and missing_chunk.exists() and corrupt_chunk.exists()


def test_cleanup_is_confirmed_audited_idempotent_and_isolates_unsafe_recording(tmp_path: Path):
    database, _, manager, maintenance, session = build_maintenance(tmp_path)
    _, safe_chunk = complete_recording(manager, session, "recording-safe-clean")
    _, unsafe_chunk = complete_recording(manager, session, "recording-unsafe-clean")
    outside = tmp_path / "outside.chunk"
    outside.write_bytes(b"must-survive")
    with database.connect() as conn:
        conn.execute(
            "UPDATE recording_chunks SET local_path=? WHERE recording_id=?",
            (str(outside), "recording-unsafe-clean"),
        )
    age_verification(database, "recording-safe-clean", 20)
    age_verification(database, "recording-unsafe-clean", 20)
    preview = maintenance.preview()
    with pytest.raises(PermissionError):
        maintenance.cleanup(preview["preview_id"], confirmed=False)

    result = maintenance.cleanup(preview["preview_id"], confirmed=True)
    replay = maintenance.cleanup(preview["preview_id"], confirmed=True)

    assert result["cleaned_recordings"] == 1
    assert result["failures"][0]["recording_id"] == "recording-unsafe-clean"
    assert replay == {**result, "replayed": True}
    assert not safe_chunk.exists()
    assert unsafe_chunk.exists() and outside.read_bytes() == b"must-survive"
    with database.connect() as conn:
        audits = conn.execute(
            "SELECT payload_json FROM audit_log WHERE action='cleanup_recording_chunks'"
        ).fetchall()
    assert len(audits) == 1
    assert "local_path" not in audits[0]["payload_json"]


def test_symlink_chunk_is_never_followed(tmp_path: Path):
    database, _, manager, maintenance, session = build_maintenance(tmp_path)
    _, chunk = complete_recording(manager, session, "recording-symlink")
    outside = tmp_path / "outside-secret"
    outside.write_bytes(b"secret")
    chunk.unlink()
    chunk.symlink_to(outside)
    age_verification(database, "recording-symlink", 20)
    preview = maintenance.preview()

    result = maintenance.cleanup(preview["preview_id"], confirmed=True)

    assert result["cleaned_recordings"] == 0
    assert result["failures"]
    assert chunk.is_symlink() and outside.read_bytes() == b"secret"


def test_cleanup_rejects_tampered_and_expired_previews(tmp_path: Path):
    database, _, manager, maintenance, session = build_maintenance(tmp_path)
    complete_recording(manager, session, "recording-preview-expiry")
    age_verification(database, "recording-preview-expiry", 20)
    preview = maintenance.preview()

    with pytest.raises(ValueError, match="过期"):
        maintenance.cleanup("0" * 32, confirmed=True)
    maintenance._previews[preview["preview_id"]]["expires_at"] = (
        datetime.now(UTC) - timedelta(seconds=1)
    ).isoformat()
    with pytest.raises(ValueError, match="过期"):
        maintenance.cleanup(preview["preview_id"], confirmed=True)


def test_concurrent_duplicate_cleanup_runs_once_and_replays_result(tmp_path: Path):
    database, _, manager, maintenance, session = build_maintenance(tmp_path)
    complete_recording(manager, session, "recording-concurrent-cleanup")
    age_verification(database, "recording-concurrent-cleanup", 20)
    preview = maintenance.preview()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: maintenance.cleanup(preview["preview_id"], confirmed=True),
                range(2),
            )
        )

    assert sorted(item["replayed"] for item in results) == [False, True]
    assert {item["cleaned_recordings"] for item in results} == {1}
    with database.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS count FROM audit_log WHERE action='cleanup_recording_chunks'"
        ).fetchone()["count"]
    assert count == 1


def test_cleanup_audit_failure_keeps_database_state_and_next_preview_recovers(tmp_path: Path):
    database, _, manager, maintenance, session = build_maintenance(tmp_path)
    _, chunk = complete_recording(manager, session, "recording-audit-recovery")
    age_verification(database, "recording-audit-recovery", 20)
    with database.connect() as conn:
        conn.execute(
            """CREATE TRIGGER reject_cleanup_audit BEFORE INSERT ON audit_log
               WHEN NEW.action='cleanup_recording_chunks'
               BEGIN SELECT RAISE(ABORT, 'audit write failed'); END"""
        )
    first_preview = maintenance.preview()

    failed = maintenance.cleanup(first_preview["preview_id"], confirmed=True)

    assert failed["cleaned_recordings"] == 0
    assert failed["reclaimed_bytes"] == len(b"chunk")
    assert failed["failures"][0]["recording_id"] == "recording-audit-recovery"
    assert failed["failures"][0]["recoverable"] is True
    assert not chunk.exists()
    with database.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS count FROM recording_chunks WHERE recording_id=?",
            ("recording-audit-recovery",),
        ).fetchone()["count"] == 1
        assert conn.execute(
            "SELECT COUNT(*) AS count FROM audit_log WHERE action='cleanup_recording_chunks'"
        ).fetchone()["count"] == 0
        conn.execute("DROP TRIGGER reject_cleanup_audit")

    retry_preview = maintenance.preview()
    recovered = maintenance.cleanup(retry_preview["preview_id"], confirmed=True)

    assert recovered["cleaned_recordings"] == 1
    assert recovered["reclaimed_bytes"] == 0
    with database.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS count FROM recording_chunks WHERE recording_id=?",
            ("recording-audit-recovery",),
        ).fetchone()["count"] == 0
        assert conn.execute(
            "SELECT COUNT(*) AS count FROM audit_log WHERE action='cleanup_recording_chunks'"
        ).fetchone()["count"] == 1


def test_preview_and_replay_caches_are_bounded_and_expire(tmp_path: Path):
    _, _, _, maintenance, _ = build_maintenance(tmp_path)
    for _ in range(maintenance._PREVIEW_LIMIT + 5):
        maintenance.preview()
    assert len(maintenance._previews) <= maintenance._PREVIEW_LIMIT
    maintenance._cleanup_results["expired"] = {
        "result": {"cleaned_recordings": 0},
        "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    }
    maintenance.preview()
    assert "expired" not in maintenance._cleanup_results


def test_diagnostic_redaction_covers_structures_headers_urls_and_tokens() -> None:
    sample = {
        "Authorization": "Bearer abc.def",
        "nested": {
            "api_key": "sk-super-secret-token",
            "message": "GET https://example.test/a?token=url-secret&ok=1\nCookie: sid=private\nx-CuStOm-TrAcE: custom-value\n{\"custom_headers\":{\"Tenant\":\"tenant-value\"}}",
        },
    }
    rendered = json.dumps(redact_structure(sample), ensure_ascii=False)
    rendered += redact_text("OPENAI_API_KEY=plain-secret Authorization: Bearer xyz sk-abcdefghijk")

    for secret in ("abc.def", "super-secret", "url-secret", "sid=private", "custom-value", "tenant-value", "plain-secret", "abcdefghijk", "xyz"):
        assert secret not in rendered
    assert REDACTED in rendered


def test_diagnostic_bundle_is_whitelisted_capped_private_and_cleans_failures(tmp_path: Path):
    database, automation, _, _, _ = build_maintenance(tmp_path)
    log_dir = tmp_path / "data" / "logs"
    log_dir.mkdir()
    (log_dir / "backend.log").write_text("x" * 300_000 + "\nAuthorization: Bearer secret-value\nX-Custom-Secret: custom-header-value\n", encoding="utf-8")
    (log_dir / "unrelated.txt").write_text("course transcript secret", encoding="utf-8")
    exporter = DiagnosticExporter(database, automation, tmp_path / "data", app_version="test")

    path, download_name = exporter.build()

    assert download_name.startswith("KnowledgeDebt-diagnostics-")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with zipfile.ZipFile(path) as archive:
        assert set(archive.namelist()) == {"manifest.json", "contents.json", "logs/backend.log"}
        log = archive.read("logs/backend.log")
        assert len(log) <= exporter.LOG_OUTPUT_LIMIT
        assert b"secret-value" not in log and b"custom-header-value" not in log and REDACTED.encode() in log
        combined = b"".join(archive.read(name) for name in archive.namelist())
        assert b"course transcript secret" not in combined

    with pytest.raises(RuntimeError):
        exporter.build(fail_after_write=True)
    assert not list(exporter.output_dir.glob("*.tmp"))
    assert len(list(exporter.output_dir.glob("*.zip"))) == 1
    exporter.discard(path)
    assert not path.exists()


def test_diagnostic_export_uses_native_auth_streaming_and_removes_server_copy(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "native-data",
        ai_provider="openai_compatible",
        asr_provider="openai_compatible",
        api_key=None,
        base_url="https://api.openai.com/v1",
        ai_model="test-ai",
        asr_model="test-asr",
        access_token="local-access-token",
    )
    app = create_app(settings=settings, recording_assembler=StrictAssembler())
    with TestClient(app) as client:
        assert client.post("/maintenance/diagnostics/export").status_code == 401
        response = client.post(
            "/maintenance/diagnostics/export",
            headers={"Authorization": "Bearer local-access-token"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert "KnowledgeDebt-diagnostics-" in response.headers["content-disposition"]
    assert not list((settings.data_dir / "diagnostics").glob("*.zip"))
    with app.state.db.connect() as conn:
        audit = conn.execute(
            "SELECT payload_json FROM audit_log WHERE action='export_diagnostics'"
        ).fetchone()
    assert audit and "local_path" not in audit["payload_json"] and "local-access-token" not in audit["payload_json"]


def test_maintenance_api_maps_conflicts_and_hides_diagnostic_failures(tmp_path: Path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path / "api-data",
        ai_provider="openai_compatible",
        asr_provider="openai_compatible",
        api_key=None,
        base_url="https://api.openai.com/v1",
        ai_model="test-ai",
        asr_model="test-asr",
        app_version="9.8.7-test",
    )
    app = create_app(settings=settings, recording_assembler=StrictAssembler())
    with TestClient(app) as client:
        unconfirmed = client.post(
            "/maintenance/storage/cleanup",
            json={"preview_id": "0" * 32, "confirmed": False},
        )
        expired = client.post(
            "/maintenance/storage/cleanup",
            json={"preview_id": "0" * 32, "confirmed": True},
        )
        assert unconfirmed.status_code == 422
        assert expired.status_code == 409
        assert client.get("/health").json()["version"] == "9.8.7-test"

        def fail_export() -> tuple[Path, str]:
            raise RuntimeError("sk-never-leak-this /Users/private/secret")

        monkeypatch.setattr(app.state.diagnostics, "build", fail_export)
        failed_export = client.post("/maintenance/diagnostics/export")
        assert failed_export.status_code == 500
        assert "never-leak" not in failed_export.text
        assert "/Users/private" not in failed_export.text


def test_cleanup_api_never_exposes_storage_paths_on_directory_failure(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "path-safety-data",
        ai_provider="openai_compatible",
        asr_provider="openai_compatible",
        api_key=None,
        base_url="https://api.openai.com/v1",
        ai_model="test-ai",
        asr_model="test-asr",
    )
    app = create_app(settings=settings, recording_assembler=StrictAssembler())
    with TestClient(app) as client:
        course = client.post("/courses", json={"name": "路径安全课程"}).json()
        session = client.post(
            f"/courses/{course['id']}/sessions", json={"title": "路径安全"}
        ).json()
        recording_id = "recording-api-path-safety"
        assert client.post(
            f"/sessions/{session['id']}/recordings",
            json={
                "recording_id": recording_id,
                "mime_type": "audio/webm",
                "filename": "safe.webm",
                "auto_transcribe": False,
            },
        ).status_code == 201
        assert client.put(
            f"/recordings/{recording_id}/chunks/0",
            files={"file": ("0.chunk", b"chunk", "audio/webm")},
        ).status_code == 200
        assert client.post(
            f"/recordings/{recording_id}/finalize",
            json={"last_sequence": 0, "duration_seconds": 5},
        ).status_code == 200
        age_verification(app.state.db, recording_id, 20)
        preview = client.post("/maintenance/storage/preview").json()
        chunk_root = app.state.recordings.chunk_root
        moved_root = chunk_root.with_name("recording-chunks-held-for-test")
        chunk_root.rename(moved_root)
        chunk_root.write_text("not a directory", encoding="utf-8")
        try:
            response = client.post(
                "/maintenance/storage/cleanup",
                json={"preview_id": preview["preview_id"], "confirmed": True},
            )
        finally:
            chunk_root.unlink()
            moved_root.rename(chunk_root)

    assert response.status_code == 200
    failure = response.json()["failures"][0]
    assert failure["code"] == "chunk_storage_missing"
    assert str(tmp_path) not in response.text
    assert "/recording-chunks" not in response.text
    assert "Errno" not in response.text


def test_diagnostic_zip_applies_final_redaction_pass(tmp_path: Path, monkeypatch):
    database, automation, _, _, _ = build_maintenance(tmp_path)
    log_dir = tmp_path / "data" / "logs"
    log_dir.mkdir()
    (log_dir / "web.log").write_text("safe", encoding="utf-8")
    exporter = DiagnosticExporter(database, automation, tmp_path / "data", app_version="test")
    monkeypatch.setattr(
        exporter,
        "_safe_log_tail",
        lambda _path, _limit: "X-Last-Pass: must-not-survive",
    )

    path, _ = exporter.build()

    with zipfile.ZipFile(path) as archive:
        body = archive.read("logs/web.log")
    assert b"must-not-survive" not in body
    assert REDACTED.encode() in body
