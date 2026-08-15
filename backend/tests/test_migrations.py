import json
import sqlite3
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from app.database import Database
from app.models import CourseCreate, SessionCreate


def test_alembic_upgrade_creates_complete_schema(tmp_path: Path):
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(backend_dir / "alembic.ini")
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path / 'migration.sqlite3'}")

    command.upgrade(config, "head")

    inspector = inspect(create_engine(config.get_main_option("sqlalchemy.url")))
    tables = set(inspector.get_table_names())
    assert {
        "alembic_version",
        "courses",
        "sessions",
        "resources",
        "document_chunks",
        "knowledge_points",
        "mastery_evidence",
        "jobs",
    } <= tables
    assert {"start_offset", "end_offset", "capture_range_json"} <= {
        column["name"] for column in inspector.get_columns("resources")
    }


def test_sqlalchemy_repository_adapter_runs_application_queries(tmp_path: Path):
    database = Database(f"sqlite:///{tmp_path / 'adapter.sqlite3'}")
    course = database.create_course(CourseCreate(name="Distributed Systems"))
    session = database.create_session(course["id"], SessionCreate(title="Consensus"))

    assert database.get_course(course["id"])["name"] == "Distributed Systems"
    assert database.get_session(session["id"])["title"] == "Consensus"


def test_alembic_upgrades_legacy_sqlite_data_in_place(tmp_path: Path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE courses (
                 id TEXT PRIMARY KEY, name TEXT, description TEXT, semester TEXT, teacher TEXT,
                 schedule TEXT, profile_json TEXT, created_at TEXT, updated_at TEXT
               )"""
        )
        connection.execute(
            "INSERT INTO courses VALUES ('old', 'Legacy', '', '', NULL, NULL, ?, 'old', 'old')",
            (json.dumps({"audio": 50, "slides": 50}),),
        )
        connection.execute(
            """CREATE TABLE resources (
                 id TEXT PRIMARY KEY, session_id TEXT, type TEXT, evidence_level TEXT, name TEXT,
                 mime_type TEXT, local_path TEXT, external_url TEXT, extracted_text TEXT,
                 coverage REAL, quality REAL, relevance REAL, duration_seconds REAL,
                 upload_state TEXT, created_at TEXT, updated_at TEXT
               )"""
        )
        connection.execute(
            """CREATE TABLE transcript_segments (
                 id TEXT PRIMARY KEY, resource_id TEXT, start_time REAL, end_time REAL,
                 text TEXT, created_at TEXT
               )"""
        )

    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(backend_dir / "alembic.ini")
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, "head")

    with sqlite3.connect(path) as connection:
        resource_columns = {row[1] for row in connection.execute("PRAGMA table_info(resources)")}
        profile = json.loads(
            connection.execute("SELECT profile_json FROM courses WHERE id='old'").fetchone()[0]
        )
    assert {"storage_provider", "start_offset", "end_offset", "capture_range_json"} <= resource_columns
    assert set(profile) == {"classroom", "official_session", "course_context", "supplementary"}
