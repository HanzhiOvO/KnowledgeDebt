import asyncio
import json
from pathlib import Path

import pytest

from app.automation import AutomationRepository
from app.database import Database
from app.models import CourseCreate, SessionCreate, TranscriptSegment
from app.provider_registry import LoggedAIProvider
from app.schedule import ZJSUFixtureParser
from app.secrets import SecretStore
from app.storage import LocalStorageProvider
from app.transcription import FFmpegUnavailable, MediaPreparer, TranscriptionOrchestrator


def build_session(database: Database) -> tuple[dict, dict]:
    course = database.create_course(CourseCreate(name="编译原理", semester="2026 秋"))
    session = database.create_session(course["id"], SessionCreate(title="待识别课堂"))
    return course, session


def test_plaintext_provider_secret_is_refused_without_encryption_key(monkeypatch):
    monkeypatch.setenv("KD_TEST_PROVIDER_KEY", "only-in-process")
    store = SecretStore(None)

    with pytest.raises(ValueError, match="Refusing to store a plaintext credential"):
        store.encrypt("must-not-hit-the-database")

    assert store.resolve(None, "env:KD_TEST_PROVIDER_KEY") == "only-in-process"
    with pytest.raises(ValueError, match="Only env:VARIABLE"):
        store.resolve(None, "plain-reference")


def test_provider_profile_reports_environment_secret_only_when_value_exists(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KD_OPTIONAL_PROVIDER_KEY", raising=False)
    automation = AutomationRepository(Database(tmp_path / "providers.sqlite3"))
    profile = automation.create_provider_profile(
        {
            "name": "测试 Profile",
            "vendor": "test",
            "adapter": "openai_compatible",
            "credential_reference": "env:KD_OPTIONAL_PROVIDER_KEY",
            "default_model": "test-model",
            "capabilities": ["structured_generation"],
            "external": True,
        }
    )
    assert profile["credential_configured"] is False

    monkeypatch.setenv("KD_OPTIONAL_PROVIDER_KEY", "available-in-process")
    assert automation.get_provider_profile(profile["id"])["credential_configured"] is True


def test_provider_ledger_records_metadata_without_sensitive_payload(tmp_path: Path):
    class ExternalAnalysisProvider:
        requires_external_upload = True

        async def analyze_session(self, session: dict, evidence: list[dict]) -> str:
            assert evidence[0]["text"] == "private classroom payload"
            return "ok"

    automation = AutomationRepository(Database(tmp_path / "ledger.sqlite3"))
    logged = LoggedAIProvider(
        ExternalAnalysisProvider(),
        {
            "id": "external-profile",
            "name": "外部测试 AI",
            "default_model": "model-without-price-metadata",
        },
        automation,
    )
    result = asyncio.run(
        logged.analyze_session(
            {"id": "session-1"},
            [{"text": "private classroom payload", "authorization": "never-log-this"}],
        )
    )

    assert result == "ok"
    usage = automation.provider_usage()
    assert usage["request_count"] == 1
    assert usage["unknown_cost_count"] == 1
    assert usage["items"][0]["operation"] == "analysis"
    assert "private classroom payload" not in json.dumps(usage, ensure_ascii=False)
    assert "never-log-this" not in json.dumps(usage, ensure_ascii=False)


def test_zjsu_fixture_requires_real_period_clock_and_expands_odd_weeks():
    parser = ZJSUFixtureParser()
    fixture = {
        "schema": "knowledgedebt.zjsu.schedule.fixture.v1",
        "term": {
            "name": "2026-2027-1",
            "starts_on": "2026-09-07",
            "ends_on": "2027-01-10",
            "timezone": "Asia/Shanghai",
        },
        "period_times": {"1": ["08:05", "08:50"], "2": ["08:55", "09:40"]},
        "courses": [
            {
                "external_id": "rule-compiler",
                "course_name": "编译原理",
                "weekday": 1,
                "start_period": 1,
                "end_period": 2,
                "weeks": "1-4周(单)",
                "odd_even": "odd",
                "teacher": "测试教师",
                "room": "A101",
            }
        ],
    }

    parsed = parser.parse(json.dumps(fixture, ensure_ascii=False))
    assert [item["occurrence_date"] for item in parsed["occurrences"]] == [
        "2026-09-07",
        "2026-09-21",
    ]
    assert parsed["occurrences"][0]["starts_at"].endswith("+08:00")
    assert "T08:05:00" in parsed["occurrences"][0]["starts_at"]

    fixture.pop("period_times")
    with pytest.raises(ValueError, match="will not guess"):
        parser.parse(json.dumps(fixture, ensure_ascii=False))


def test_occurrence_materialization_is_lazy_cancel_safe_and_idempotent(tmp_path: Path):
    database = Database(tmp_path / "schedule.sqlite3")
    automation = AutomationRepository(database)
    term = automation.create_term(
        {
            "name": "2026 秋",
            "starts_on": "2026-09-07",
            "ends_on": "2027-01-10",
            "timezone": "Asia/Shanghai",
            "current": True,
        }
    )
    rule = automation.upsert_schedule_rule(
        {
            "term_id": term["id"],
            "course_name": "编译原理",
            "weekday": 1,
            "start_period": 1,
            "end_period": 2,
            "weeks": [1],
            "odd_even": "all",
            "external_id": "compiler-rule",
            "aliases": [],
        }
    )
    scheduled = automation.upsert_occurrence(
        {
            "rule_id": rule["id"],
            "occurrence_date": "2026-09-07",
            "starts_at": "2026-09-07T08:05:00+08:00",
            "ends_at": "2026-09-07T09:40:00+08:00",
            "status": "scheduled",
            "source_kind": "regular",
            "external_id": "compiler-rule:2026-09-07",
        }
    )
    cancelled = automation.upsert_occurrence(
        {
            "rule_id": rule["id"],
            "occurrence_date": "2026-09-14",
            "starts_at": "2026-09-14T08:05:00+08:00",
            "ends_at": "2026-09-14T09:40:00+08:00",
            "status": "cancelled",
            "source_kind": "adjustment",
            "external_id": "compiler-rule:cancelled",
        }
    )

    assert database.list_sessions() == []
    with pytest.raises(ValueError, match="cancelled occurrence"):
        automation.materialize_occurrence(cancelled["id"], "opened")

    first = automation.materialize_occurrence(scheduled["id"], "opened")
    second = automation.materialize_occurrence(scheduled["id"], "evidence")
    assert first["id"] == second["id"]
    assert first["title"] == "编译原理-2026-09-07-待识别"
    assert len(database.list_sessions()) == 1


def test_inbox_adoption_and_review_decisions_are_idempotent(tmp_path: Path):
    database = Database(tmp_path / "review.sqlite3")
    automation = AutomationRepository(database)
    _, session = build_session(database)
    item = automation.create_inbox_item(
        {
            "name": "课堂录音.webm",
            "mime_type": "audio/webm",
            "type": "audio",
            "storage_provider": "local",
            "storage_key": "inbox/recording.webm",
            "local_path": str(tmp_path / "recording.webm"),
            "extracted_text": "",
        }
    )
    resource = automation.adopt_inbox_item(item["id"], session["id"])
    duplicate = automation.adopt_inbox_item(item["id"], session["id"])
    assert resource["id"] == duplicate["id"]

    review = automation.create_review_item(
        "archive_match",
        "inbox_item",
        item["id"],
        "确认归档课堂",
        proposed_value=session["id"],
        confidence=0.72,
        reasons=["时间接近", "课程别名命中"],
    )
    same_pending = automation.create_review_item(
        "archive_match",
        "inbox_item",
        item["id"],
        "重复候选",
    )
    assert same_pending["id"] == review["id"]
    accepted = automation.decide_review(review["id"], "accept", "人工确认")
    repeated = automation.decide_review(review["id"], "reject", "重复请求")
    assert accepted["status"] == repeated["status"] == "accepted"


class FlakyChunkProvider:
    requires_external_upload = False

    def __init__(self):
        self.calls: list[str] = []
        self.failed_once = False

    async def transcribe(self, path: str, mime_type: str | None) -> list[TranscriptSegment]:
        self.calls.append(Path(path).name)
        if Path(path).name == "chunk-0001.flac" and not self.failed_once:
            self.failed_once = True
            raise RuntimeError("temporary ASR failure")
        return [TranscriptSegment(start_time=1, end_time=3, text=f"segment from {Path(path).name}")]


class TwoChunkPreparer:
    def __init__(self, root: Path):
        self.root = root

    def prepare(self, resource_id: str, source: Path, duration_seconds: float | None) -> list[dict]:
        chunks = []
        for position in range(2):
            path = self.root / f"chunk-{position:04d}.flac"
            path.write_bytes(b"retained test audio")
            chunks.append(
                {
                    "start_seconds": position * 60.0,
                    "end_seconds": (position + 1) * 60.0,
                    "media_path": str(path),
                }
            )
        return chunks


def test_transcription_retry_keeps_successful_chunks_and_global_timestamps(tmp_path: Path):
    database = Database(tmp_path / "transcription.sqlite3")
    automation = AutomationRepository(database)
    _, session = build_session(database)
    media_path = tmp_path / "lecture.bin"
    media_path.write_bytes(b"original media is retained")
    resource = database.add_resource(
        session["id"],
        type="audio",
        evidence_level="classroom",
        name="lecture.bin",
        mime_type="application/octet-stream",
        local_path=str(media_path),
        duration_seconds=120,
    )
    automation.ensure_resource_automation(resource["id"])
    provider = FlakyChunkProvider()
    profile = {
        "id": "local-test",
        "name": "本地测试 ASR",
        "default_model": "deterministic-fixture",
        "external": False,
        "capabilities": [],
    }
    orchestrator = TranscriptionOrchestrator(
        database,
        automation,
        LocalStorageProvider(tmp_path / "objects"),
        lambda: (profile, provider),
        TwoChunkPreparer(tmp_path),
        lambda session_id: None,
    )

    first_job, created = orchestrator.create_job(resource["id"], confirmed_external_upload=False)
    assert created is True
    first_result = asyncio.run(orchestrator.run(first_job["id"]))
    assert first_result["status"] == "failed"
    first_state = automation.get_resource_automation(resource["id"])
    assert first_state["transcription_state"] == "partial"
    assert "temporary ASR failure" in first_state["failure_reason"]

    retry_job, retry_created = orchestrator.create_job(resource["id"], confirmed_external_upload=False)
    assert retry_created is True
    retry_result = asyncio.run(orchestrator.run(retry_job["id"]))
    assert retry_result["status"] == "succeeded"
    assert provider.calls == ["chunk-0000.flac", "chunk-0001.flac", "chunk-0001.flac"]
    segments = database.list_transcript_segments(resource["id"])
    assert [(item["global_start"], item["global_end"]) for item in segments] == [(1, 3), (61, 63)]
    assert automation.provider_usage()["request_count"] == 3


def test_external_transcription_creates_no_job_before_one_time_consent(tmp_path: Path):
    database = Database(tmp_path / "consent.sqlite3")
    automation = AutomationRepository(database)
    _, session = build_session(database)
    media_path = tmp_path / "lecture.webm"
    media_path.write_bytes(b"original media")
    resource = database.add_resource(
        session["id"],
        type="audio",
        evidence_level="classroom",
        name="lecture.webm",
        mime_type="audio/webm",
        local_path=str(media_path),
        duration_seconds=12,
    )
    automation.ensure_resource_automation(resource["id"])
    provider = FlakyChunkProvider()
    external_profile = {
        "id": "external-test",
        "name": "外部测试 ASR",
        "default_model": "test-model",
        "external": True,
        "capabilities": ["long_audio"],
    }
    orchestrator = TranscriptionOrchestrator(
        database,
        automation,
        LocalStorageProvider(tmp_path / "objects"),
        lambda: (external_profile, provider),
        TwoChunkPreparer(tmp_path),
        lambda session_id: None,
    )

    with pytest.raises(PermissionError, match="explicit one-time consent"):
        orchestrator.create_job(resource["id"], confirmed_external_upload=False)
    assert database.list_jobs(session["id"]) == []
    assert automation.get_resource_automation(resource["id"])["transcription_state"] == "awaiting_consent"

    job, created = orchestrator.create_job(resource["id"], confirmed_external_upload=True)
    same_job, duplicate_created = orchestrator.create_job(resource["id"], confirmed_external_upload=True)
    assert created is True
    assert duplicate_created is False
    assert same_job["id"] == job["id"]


def test_aac_without_ffmpeg_is_retained_and_reports_actionable_error(tmp_path: Path):
    source = tmp_path / "lecture.aac"
    source.write_bytes(b"retained original aac")
    preparer = MediaPreparer(
        str(tmp_path / "missing-ffmpeg"),
        tmp_path / "chunks",
        1500,
    )

    with pytest.raises(FFmpegUnavailable, match="需要 FFmpeg"):
        preparer.prepare("resource-aac", source, 60)

    assert source.read_bytes() == b"retained original aac"
    assert not (tmp_path / "chunks" / "resource-aac").exists()
