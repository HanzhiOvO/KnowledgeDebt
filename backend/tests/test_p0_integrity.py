from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.automation import AutomationRepository
from app.config import Settings
from app.database import Database
from app.main import create_app
from app.models import CourseCreate, SessionCreate
from app.recording_media import AssembledRecording, RecordingMediaError
from app.recordings import RecordingConflict, RecordingIncomplete, RecordingManager
from app.schedule import ZJSUFixtureParser
from app.storage import LocalStorageProvider
from app.timezones import local_date


class PassthroughRecordingAssembler:
    """Keep persistence tests independent from a machine-wide FFmpeg installation."""

    def __init__(self, duration_seconds: float = 10):
        self.duration_seconds = duration_seconds

    def assemble(self, recording_id: str, chunks: list[dict]) -> AssembledRecording:
        target = Path(chunks[0]["local_path"]).parent / f".{recording_id}.test.flac"
        with target.open("wb") as output:
            for chunk in chunks:
                output.write(Path(chunk["local_path"]).read_bytes())
        return AssembledRecording(target, self.duration_seconds)

    def verify(self, path: Path) -> float:
        assert path.is_file() and path.stat().st_size > 0
        return self.duration_seconds


def build_session(database: Database) -> dict:
    course = database.create_course(CourseCreate(name="编译原理", semester="2026 秋"))
    return database.create_session(course["id"], SessionCreate(title="课堂录音"))


def recording_manager(tmp_path: Path) -> tuple[Database, AutomationRepository, RecordingManager, dict]:
    database = Database(tmp_path / "recordings.sqlite3")
    automation = AutomationRepository(database)
    session = build_session(database)
    manager = RecordingManager(
        database,
        automation,
        LocalStorageProvider(tmp_path / "objects"),
        tmp_path / "raw-chunks",
        assembler=PassthroughRecordingAssembler(),
    )
    return database, automation, manager, session


def test_recording_chunks_are_durable_idempotent_and_checked_before_finalize(tmp_path: Path):
    database, _, manager, session = recording_manager(tmp_path)
    values = {
        "recording_id": "recording-safe-1",
        "mime_type": "audio/webm",
        "filename": "课堂.webm",
        "start_offset": 60,
        "session_duration": 600,
        "auto_transcribe": True,
    }
    recording, created = manager.create(session["id"], values)
    duplicate_recording, created_again = manager.create(session["id"], values)
    assert created is True
    assert created_again is False
    assert duplicate_recording["id"] == recording["id"]

    first = b"first-webm-fragment"
    checksum = hashlib.sha256(first).hexdigest()
    accepted = manager.save_chunk(
        recording["id"], 0, first, "audio/webm", checksum, "capture-a", 0
    )
    repeated = manager.save_chunk(
        recording["id"], 0, first, "audio/webm", checksum, "capture-a", 0
    )
    assert accepted["deduplicated"] is False
    assert repeated["deduplicated"] is True
    with pytest.raises(RecordingConflict, match="不同内容"):
        manager.save_chunk(recording["id"], 0, b"changed", "audio/webm", None)
    with pytest.raises(RecordingConflict, match="录音流信息"):
        manager.save_chunk(recording["id"], 0, first, "audio/webm", None, "capture-b", 1)
    with pytest.raises(RecordingIncomplete) as incomplete:
        manager.finalize(recording["id"], 1, 10)
    assert incomplete.value.missing_sequences == [1]

    second = b"second-webm-fragment"
    manager.save_chunk(recording["id"], 1, second, "audio/webm", None, "capture-b", 1)
    resource, finalized = manager.finalize(recording["id"], 1, 10)
    repeated_resource, finalized_again = manager.finalize(recording["id"], 1, 10)
    assert finalized is True
    assert finalized_again is False
    assert repeated_resource["id"] == resource["id"]
    assert Path(resource["local_path"]).read_bytes() == first + second
    assert resource["mime_type"] == "audio/flac"
    assert resource["name"] == "课堂.flac"
    assert len(manager.get(recording["id"])["chunks"]) == 2
    assert manager.get(recording["id"])["next_stream_index"] == 2
    assert len(database.list_resources(session["id"])) == 1


def test_recording_api_waits_for_configuration_without_losing_original(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "data",
        ai_provider="openai_compatible",
        asr_provider="openai_compatible",
        api_key=None,
        base_url="https://api.openai.com/v1",
        ai_model="test-ai",
        asr_model="test-asr",
    )
    with TestClient(
        create_app(settings=settings, recording_assembler=PassthroughRecordingAssembler(5))
    ) as client:
        course = client.post("/courses", json={"name": "数据库"}).json()
        session = client.post(f"/courses/{course['id']}/sessions", json={"title": "第 1 讲"}).json()
        recording = client.post(
            f"/sessions/{session['id']}/recordings",
            json={
                "recording_id": "browser-recording-1",
                "mime_type": "audio/webm",
                "filename": "browser.webm",
                "session_duration": 600,
            },
        ).json()
        content = b"retained-browser-audio"
        response = client.put(
            f"/recordings/{recording['id']}/chunks/0",
            files={"file": ("0.chunk", content, "audio/webm")},
            headers={"X-Chunk-SHA256": hashlib.sha256(content).hexdigest()},
        )
        assert response.status_code == 200
        completed = client.post(
            f"/recordings/{recording['id']}/finalize",
            json={"last_sequence": 0, "duration_seconds": 5},
        )
        assert completed.status_code == 200
        payload = completed.json()
        assert payload["automation"]["transcription_state"] == "awaiting_configuration"
        assert "local_path" not in payload["resource"]
        retained = client.get(f"/resources/{payload['resource']['id']}/raw")
        assert retained.status_code == 200
        assert retained.content == content
        assert payload["transcription_job"] is None


def test_home_onboarding_records_recovery_and_deduplicates_active_transcription(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "data",
        ai_provider="openai_compatible",
        asr_provider="openai_compatible",
        api_key=None,
        base_url="https://api.openai.com/v1",
        ai_model="test-ai",
        asr_model="test-asr",
    )
    app = create_app(settings=settings, recording_assembler=PassthroughRecordingAssembler(5))
    with TestClient(app) as client:
        initial = client.get("/home").json()
        assert initial["onboarding"] == {
            "schedule_ready": False,
            "transcription_configured": False,
            "transcription_tested": False,
            "session_created": False,
            "recording_started": False,
        }
        assert initial["schedule_connection"]["connector"] == "zjsu_undergraduate_v9"

        course = client.post("/courses", json={"name": "操作系统"}).json()
        session = client.post(
            f"/courses/{course['id']}/sessions", json={"title": "2026-08-21-待识别"}
        ).json()
        recording = client.post(
            f"/sessions/{session['id']}/recordings",
            json={
                "recording_id": "home-recovery-recording",
                "mime_type": "audio/webm",
                "filename": "未完成课堂.webm",
            },
        ).json()

        media = client.post(
            f"/sessions/{session['id']}/resources/upload",
            data={
                "resource_type": "audio",
                "evidence_level": "classroom",
                "coverage": "1",
                "quality": "1",
                "relevance": "1",
                "auto_transcribe": "false",
            },
            files={"file": ("待转写.wav", b"audio-placeholder", "audio/wav")},
        ).json()
        job = app.state.db.create_job(
            "transcription", session_id=session["id"], resource_id=media["id"]
        )
        app.state.automation.update_resource_transcription(
            media["id"], "queued", job_id=job["id"]
        )
        review = app.state.automation.create_review_item(
            "session_topic",
            "session",
            session["id"],
            "稍后确认课堂主题",
            proposed_value="进程调度",
            confidence=0.7,
            reasons=["测试稍后处理仍计入首页待审核"],
        )
        assert client.post(
            f"/reviews/{review['id']}/decision",
            json={"action": "later", "reason": "稍后处理"},
        ).status_code == 200

        home = client.get("/home").json()
        assert datetime.fromisoformat(home["generated_at"]).tzinfo is not None
        assert home["onboarding"]["session_created"] is True
        assert home["onboarding"]["recording_started"] is True
        assert home["active_recordings"][0]["id"] == recording["id"]
        assert home["active_recordings"][0]["session_title"] == session["title"]
        assert all(item["resource_id"] != media["id"] for item in home["pending_automation"])
        assert [item["id"] for item in home["jobs"]].count(job["id"]) == 1
        assert home["pending_review_count"] == 1


def test_media_upload_obeys_global_auto_transcription_preference(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "data",
        ai_provider="openai_compatible",
        asr_provider="openai_compatible",
        api_key=None,
        base_url="https://api.openai.com/v1",
        ai_model="test-ai",
        asr_model="test-asr",
    )
    app = create_app(settings=settings)
    with TestClient(app) as client:
        course = client.post("/courses", json={"name": "数据库系统"}).json()
        session = client.post(
            f"/courses/{course['id']}/sessions", json={"title": "数据库系统-待识别"}
        ).json()

        assert client.patch(
            "/settings/application", json={"auto_transcribe": False}
        ).status_code == 200
        disabled = client.post(
            f"/sessions/{session['id']}/resources/upload",
            data={"resource_type": "audio", "evidence_level": "classroom"},
            files={"file": ("关闭自动转写.wav", b"retained-audio-one", "audio/wav")},
        )
        assert disabled.status_code == 201
        disabled_body = disabled.json()
        assert disabled_body["automation"]["auto_transcribe"] is False
        assert disabled_body["automation"]["transcription_state"] == "saved"
        assert disabled_body["transcription_job"] is None

        assert client.patch(
            "/settings/application", json={"auto_transcribe": True}
        ).status_code == 200
        enabled = client.post(
            f"/sessions/{session['id']}/resources/upload",
            data={"resource_type": "audio", "evidence_level": "classroom"},
            files={"file": ("开启自动转写.wav", b"retained-audio-two", "audio/wav")},
        )
        assert enabled.status_code == 201
        enabled_body = enabled.json()
        assert enabled_body["automation"]["auto_transcribe"] is True
        assert enabled_body["automation"]["transcription_state"] == "awaiting_configuration"
        assert enabled_body["transcription_job"] is None

        assert len(app.state.db.list_resources(session["id"])) == 2


def test_recording_chunks_survive_backend_restart_and_concurrent_retries(tmp_path: Path):
    database, automation, manager, session = recording_manager(tmp_path)
    values = {
        "recording_id": "recording-restart-1",
        "mime_type": "audio/webm",
        "filename": "重启恢复.webm",
        "start_offset": 0,
        "session_duration": 600,
        "auto_transcribe": True,
    }
    recording, _ = manager.create(session["id"], values)
    content = b"durable-concurrent-fragment"
    checksum = hashlib.sha256(content).hexdigest()

    def submit(_: int) -> dict:
        return manager.save_chunk(
            recording["id"], 0, content, "audio/webm", checksum, "capture-a", 0
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(submit, range(16)))
    assert sum(not item["deduplicated"] for item in responses) == 1
    assert len(manager.get(recording["id"])["chunks"]) == 1

    restarted = RecordingManager(
        Database(tmp_path / "recordings.sqlite3"),
        AutomationRepository(Database(tmp_path / "recordings.sqlite3")),
        LocalStorageProvider(tmp_path / "objects"),
        tmp_path / "raw-chunks",
        assembler=PassthroughRecordingAssembler(5),
    )
    recovered = restarted.list_incomplete(session["id"])
    assert recovered[0]["received_sequences"] == [0]
    resource, created = restarted.finalize(recording["id"], 0, 5)
    assert created is True
    assert Path(resource["local_path"]).read_bytes() == content


def test_missing_ffmpeg_never_discards_raw_recording_chunks(tmp_path: Path):
    database = Database(tmp_path / "missing-ffmpeg.sqlite3")
    automation = AutomationRepository(database)
    session = build_session(database)
    manager = RecordingManager(
        database,
        automation,
        LocalStorageProvider(tmp_path / "objects"),
        tmp_path / "raw-chunks",
        ffmpeg_path=str(tmp_path / "missing-runtime" / "ffmpeg"),
    )
    recording, _ = manager.create(
        session["id"],
        {
            "recording_id": "recording-no-ffmpeg",
            "mime_type": "audio/webm",
            "filename": "课堂.webm",
            "auto_transcribe": True,
        },
    )
    manager.save_chunk(recording["id"], 0, b"raw-media", "audio/webm", None)
    raw_path = next((tmp_path / "raw-chunks" / recording["id"]).glob("*.chunk"))
    with pytest.raises(RecordingMediaError, match="原始分片"):
        manager.finalize(recording["id"], 0, 5)
    assert raw_path.read_bytes() == b"raw-media"
    assert manager.get(recording["id"])["status"] == "failed"
    assert database.list_resources(session["id"]) == []


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="需要真实 FFmpeg 才能验证多个浏览器容器的安全封装",
)
def test_real_ffmpeg_packages_two_resumed_webm_streams(tmp_path: Path):
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg
    source_files: list[Path] = []
    for index, frequency in enumerate((440, 660)):
        source = tmp_path / f"stream-{index}.webm"
        command = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration=1",
            "-c:a",
            "libopus",
            str(source),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=30)
        except subprocess.CalledProcessError as exc:
            pytest.skip(f"当前 FFmpeg 缺少 WebM/Opus 编码能力：{exc.stderr!r}")
        source_files.append(source)

    database = Database(tmp_path / "real-media.sqlite3")
    automation = AutomationRepository(database)
    session = build_session(database)
    manager = RecordingManager(
        database,
        automation,
        LocalStorageProvider(tmp_path / "objects"),
        tmp_path / "raw-chunks",
        ffmpeg_path=ffmpeg,
    )
    recording, _ = manager.create(
        session["id"],
        {
            "recording_id": "recording-real-resume",
            "mime_type": "audio/webm;codecs=opus",
            "filename": "跨刷新课堂.webm",
            "auto_transcribe": False,
        },
    )
    sequence = 0
    for stream_index, source in enumerate(source_files):
        content = source.read_bytes()
        midpoint = len(content) // 2
        for fragment in (content[:midpoint], content[midpoint:]):
            manager.save_chunk(
                recording["id"],
                sequence,
                fragment,
                "audio/webm;codecs=opus",
                None,
                f"capture-{stream_index}",
                stream_index,
            )
            sequence += 1

    resource, created = manager.finalize(recording["id"], sequence - 1, 2)
    assert created is True
    assert resource["mime_type"] == "audio/flac"
    assert 1.8 <= resource["duration_seconds"] <= 2.2
    assert Path(resource["local_path"]).stat().st_size > 0
    assert len(list((tmp_path / "raw-chunks" / recording["id"]).glob("*.chunk"))) == 4


def schedule_fixture(*, start_period: int = 1, include_course: bool = True, extra: bool = False) -> dict:
    courses = []
    if include_course:
        courses.append(
            {
                "external_id": "compiler-rule",
                "course_name": "编译原理",
                "weekday": 1,
                "start_period": start_period,
                "end_period": start_period,
                "weeks": [1, 2],
            }
        )
    if extra:
        courses.append(
            {
                "external_id": "database-rule",
                "course_name": "数据库",
                "weekday": 2,
                "start_period": 1,
                "end_period": 1,
                "weeks": [1],
            }
        )
    return {
        "schema": "knowledgedebt.zjsu.schedule.fixture.v1",
        "term": {
            "name": "2026 秋",
            "starts_on": "2026-09-07",
            "ends_on": "2027-01-10",
            "timezone": "Asia/Shanghai",
        },
        "period_times": {
            "1": ["08:00", "08:45"],
            "2": ["08:50", "09:35"],
        },
        "courses": courses,
    }


def test_authoritative_schedule_snapshot_handles_changes_removals_retries_and_rollback(tmp_path: Path):
    automation = AutomationRepository(Database(tmp_path / "schedule-sync.sqlite3"))
    parser = ZJSUFixtureParser()
    now = datetime(2026, 8, 20, 8, tzinfo=UTC)
    first_parsed = parser.parse(json.dumps(schedule_fixture()))
    first = automation.create_schedule_sync_batch("zjsu", "zjsu_fixture", first_parsed, now=now)
    automation.apply_schedule_sync_batch(first["id"], now=now)
    original = automation.list_occurrences()[0]

    changed_parsed = parser.parse(json.dumps(schedule_fixture(start_period=2)))
    changed = automation.create_schedule_sync_batch("zjsu", "zjsu_fixture", changed_parsed, now=now)
    assert changed["diff"]["summary"]["modified"] == 2
    automation.apply_schedule_sync_batch(changed["id"], now=now)
    changed_occurrence = next(
        item for item in automation.list_occurrences() if item["external_id"] == original["external_id"]
    )
    assert changed_occurrence["id"] == original["id"]
    assert "T08:50:00" in changed_occurrence["starts_at"]
    duplicate = automation.create_schedule_sync_batch("zjsu", "zjsu_fixture", changed_parsed, now=now)
    assert duplicate["id"] == changed["id"]
    assert duplicate["status"] == "applied"

    removed_parsed = parser.parse(json.dumps(schedule_fixture(include_course=False)))
    removed = automation.create_schedule_sync_batch("zjsu", "zjsu_fixture", removed_parsed, now=now)
    assert removed["diff"]["summary"]["removed"] == 2
    automation.apply_schedule_sync_batch(removed["id"], now=now)
    assert {item["sync_status"] for item in automation.list_occurrences()} == {"removed"}

    interrupted_parsed = parser.parse(json.dumps(schedule_fixture(extra=True)))
    interrupted = automation.create_schedule_sync_batch(
        "zjsu", "zjsu_fixture", interrupted_parsed, now=now
    )
    with pytest.raises(RuntimeError, match="simulated"):
        automation.apply_schedule_sync_batch(interrupted["id"], now=now, fail_after=1)
    assert automation.get_schedule_sync_batch(interrupted["id"])["status"] == "failed"
    assert not any(rule["external_id"] == "database-rule" for rule in automation.list_schedule_rules())
    automation.apply_schedule_sync_batch(interrupted["id"], now=now)
    assert any(rule["external_id"] == "database-rule" for rule in automation.list_schedule_rules())


def test_first_authoritative_snapshot_adopts_recognizable_legacy_fixture_rows(tmp_path: Path):
    database = Database(tmp_path / "legacy-schedule-source.sqlite3")
    automation = AutomationRepository(database)
    parser = ZJSUFixtureParser()
    now = datetime(2026, 8, 20, 8, tzinfo=UTC)
    parsed = parser.parse(json.dumps(schedule_fixture()))
    term_data = parsed["term"]
    term = automation.create_term(
        {
            "name": term_data["name"],
            "starts_on": term_data["starts_on"],
            "ends_on": term_data["ends_on"],
            "timezone": term_data["timezone"],
        }
    )
    legacy_rule = automation.upsert_schedule_rule({**parsed["rules"][0], "term_id": term["id"]})
    first_occurrence = parsed["occurrences"][0]
    legacy_occurrence = automation.upsert_occurrence(
        {
            **{key: value for key, value in first_occurrence.items() if key != "rule_external_id"},
            "rule_id": legacy_rule["id"],
        }
    )
    assert legacy_occurrence["source"] == "manual"

    empty_snapshot = parser.parse(json.dumps(schedule_fixture(include_course=False)))
    batch = automation.create_schedule_sync_batch(
        "zjsu_undergraduate_v9", "zjsu_fixture", empty_snapshot, now=now
    )
    assert batch["diff"]["summary"]["removed"] == 1
    assert batch["diff"]["legacy_adoption"]["occurrence_ids"] == [legacy_occurrence["id"]]
    assert any("旧版课表实例" in item["reason"] for item in batch["diff"]["conflicts"])

    automation.apply_schedule_sync_batch(batch["id"], now=now)
    migrated = next(
        item for item in automation.list_occurrences() if item["id"] == legacy_occurrence["id"]
    )
    assert migrated["source"] == "zjsu_fixture"
    assert migrated["sync_status"] == "removed"

    # Once an authoritative snapshot has been applied, later manual rows stay manual.
    manual_rule = automation.upsert_schedule_rule(
        {
            "term_id": term["id"],
            "course_name": "个人学习安排",
            "weekday": 3,
            "start_period": 1,
            "end_period": 1,
            "weeks": [1],
            "external_id": "manual-study",
        }
    )
    manual_occurrence = automation.upsert_occurrence(
        {
            "rule_id": manual_rule["id"],
            "occurrence_date": "2026-09-09",
            "starts_at": "2026-09-09T08:00:00+08:00",
            "ends_at": "2026-09-09T08:45:00+08:00",
            "external_id": "manual-study:2026-09-09",
        }
    )
    changed_empty_snapshot = {**empty_snapshot, "term": {**empty_snapshot["term"], "current": False}}
    next_batch = automation.create_schedule_sync_batch(
        "zjsu_undergraduate_v9", "zjsu_fixture", changed_empty_snapshot, now=now
    )
    assert next_batch["diff"]["legacy_adoption"]["occurrence_ids"] == []
    automation.apply_schedule_sync_batch(next_batch["id"], now=now)
    still_manual = next(
        item for item in automation.list_occurrences() if item["id"] == manual_occurrence["id"]
    )
    assert still_manual["source"] == "manual"
    assert still_manual["sync_status"] == "active"


def test_review_later_can_be_reopened_and_every_transition_is_audited(tmp_path: Path):
    database = Database(tmp_path / "review-transition.sqlite3")
    automation = AutomationRepository(database)
    review = automation.create_review_item("session_topic", "session", "session-1", "确认主题")
    later = automation.decide_review(review["id"], "later", "下课后再看")
    assert later["status"] == "later"
    assert [item["id"] for item in automation.list_review_items("later")] == [review["id"]]
    pending = automation.decide_review(review["id"], "pending", "重新处理")
    accepted = automation.decide_review(review["id"], "accept", "确认")
    repeated = automation.decide_review(review["id"], "accept", "重复请求")
    assert pending["status"] == "pending"
    assert accepted["status"] == repeated["status"] == "accepted"
    with database.connect() as conn:
        audit_count = conn.execute(
            "SELECT COUNT(*) AS count FROM audit_log WHERE subject_id=?", (review["id"],)
        ).fetchone()["count"]
    assert audit_count == 3


def test_occurrence_and_inbox_concurrency_return_one_materialized_record(tmp_path: Path):
    database = Database(tmp_path / "concurrency.sqlite3")
    automation = AutomationRepository(database)
    term = automation.create_term(
        {"name": "2026 秋", "starts_on": "2026-09-07", "ends_on": "2027-01-10"}
    )
    rule = automation.upsert_schedule_rule(
        {
            "term_id": term["id"],
            "course_name": "编译原理",
            "weekday": 1,
            "start_period": 1,
            "end_period": 1,
            "weeks": [1],
            "external_id": "compiler",
        }
    )
    occurrence = automation.upsert_occurrence(
        {
            "rule_id": rule["id"],
            "occurrence_date": "2026-09-07",
            "starts_at": "2026-09-07T08:00:00+08:00",
            "ends_at": "2026-09-07T08:45:00+08:00",
            "external_id": "compiler:2026-09-07",
        }
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        session_ids = set(pool.map(lambda _: automation.materialize_occurrence(occurrence["id"], "opened")["id"], range(16)))
    assert len(session_ids) == 1
    session_id = session_ids.pop()

    item = automation.create_inbox_item(
        {
            "name": "课堂.webm",
            "type": "audio",
            "storage_provider": "local",
            "storage_key": "inbox/concurrent.webm",
        }
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        resource_ids = set(pool.map(lambda _: automation.adopt_inbox_item(item["id"], session_id)["id"], range(16)))
    assert len(resource_ids) == 1
    assert len(database.list_resources(session_id)) == 1


def test_default_timezone_does_not_shift_china_date_at_utc_midnight_boundary():
    instant = datetime(2026, 8, 20, 16, 30, tzinfo=UTC)
    assert local_date("UTC", instant).isoformat() == "2026-08-20"
    assert local_date("Asia/Shanghai", instant).isoformat() == "2026-08-21"
