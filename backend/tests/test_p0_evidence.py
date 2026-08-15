import json
import sqlite3

import pytest

from app.database import Database
from app.models import DEFAULT_PROFILE, CourseCreate, SessionCreate
from app.providers.base import ProviderOutputError
from app.service import KnowledgeService


def test_transcript_segments_are_global_evidence(tmp_path):
    database = Database(tmp_path / "evidence.sqlite3")
    course = database.create_course(CourseCreate(name="Signals"))
    session = database.create_session(course["id"], SessionCreate(title="Lecture 4"))
    resource = database.add_resource(
        session["id"],
        type="audio",
        evidence_level="classroom",
        name="part-2.webm",
        start_offset=300,
        end_offset=600,
        session_duration=1200,
        duration_seconds=300,
        capture_range=[300, 600],
    )

    database.save_transcript(resource["id"], [{"start_time": 12, "end_time": 30, "text": "Convolution."}])
    segment = database.list_transcript_segments(resource["id"])[0]

    assert segment["global_start"] == 312
    assert segment["global_end"] == 330
    assert database.get_resource(resource["id"])["capture_range"] == [300, 600]


def test_source_validation_rejects_invented_timestamps():
    evidence = [
        {
            "id": "audio-1",
            "transcript_segments": [
                {"id": "segment-1", "global_start": 120, "global_end": 150, "text": "A real segment"}
            ],
        }
    ]
    valid = {
        "resource_id": "audio-1",
        "locator_type": "transcript",
        "start_time": 120,
        "end_time": 150,
    }
    KnowledgeService._validate_sources(valid, evidence)

    with pytest.raises(ProviderOutputError, match="does not exist"):
        KnowledgeService._validate_sources({**valid, "start_time": 121}, evidence)


def test_timeline_must_match_a_real_transcript_reference():
    evidence = [
        {
            "id": "audio-1",
            "transcript_segments": [
                {"id": "segment-1", "global_start": 20, "global_end": 40, "text": "Definition"}
            ],
        }
    ]
    payload = {
        "timeline": [
            {
                "start_time": 20,
                "end_time": 40,
                "sources": [
                    {
                        "resource_id": "audio-1",
                        "locator_type": "transcript",
                        "start_time": 20,
                        "end_time": 40,
                    }
                ],
            }
        ]
    }
    KnowledgeService._validate_sources(payload, evidence)
    KnowledgeService._validate_timeline(payload, evidence)

    payload["timeline"][0]["start_time"] = 21
    with pytest.raises(ProviderOutputError, match="must match"):
        KnowledgeService._validate_timeline(payload, evidence)


def test_legacy_database_gets_recording_columns_and_channel_profile(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """CREATE TABLE courses (
                id TEXT PRIMARY KEY, name TEXT, description TEXT, semester TEXT, teacher TEXT,
                schedule TEXT, profile_json TEXT, created_at TEXT, updated_at TEXT
            )"""
        )
        conn.execute(
            """INSERT INTO courses VALUES
               ('course-1', 'Legacy', '', '', NULL, NULL, ?, '2025-01-01', '2025-01-01')""",
            (json.dumps({"audio": 35, "slides": 25}),),
        )
        conn.execute(
            """CREATE TABLE resources (
                id TEXT PRIMARY KEY, session_id TEXT, type TEXT, evidence_level TEXT, name TEXT,
                mime_type TEXT, local_path TEXT, external_url TEXT, extracted_text TEXT,
                coverage REAL, quality REAL, relevance REAL, duration_seconds REAL,
                upload_state TEXT, created_at TEXT, updated_at TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE transcript_segments (
                id TEXT PRIMARY KEY, resource_id TEXT, start_time REAL, end_time REAL,
                text TEXT, created_at TEXT
            )"""
        )

    database = Database(path)
    assert database.get_course("course-1")["profile"] == DEFAULT_PROFILE
    with database.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(resources)")}
    assert {"start_offset", "end_offset", "session_duration", "capture_range_json"} <= columns
