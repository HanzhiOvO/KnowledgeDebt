import os

import pytest

from app.database import Database
from app.models import CourseCreate, SessionCreate


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="TEST_POSTGRES_URL is not configured")
def test_postgres_adapter_runs_core_repository_flow():
    database = Database(os.environ["TEST_POSTGRES_URL"])
    course = database.create_course(CourseCreate(name="PostgreSQL verification"))
    session = database.create_session(course["id"], SessionCreate(title="Adapter smoke test"))

    assert database.get_course(course["id"])["name"] == "PostgreSQL verification"
    assert database.get_session(session["id"])["course_id"] == course["id"]
