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
