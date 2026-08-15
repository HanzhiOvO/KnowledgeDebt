from app.database import Database
from app.models import CourseCreate, SessionCreate


def test_prerequisite_relations_drive_blocking_flags(tmp_path):
    database = Database(tmp_path / "dependencies.sqlite3")
    course = database.create_course(CourseCreate(name="Calculus"))
    session = database.create_session(course["id"], SessionCreate(title="Lecture"))
    database.save_analysis(
        session["id"],
        {
            "title": "Lecture",
            "summary": "Limits before derivatives",
            "topics": [],
            "timeline": [],
            "teacher_emphasis": [],
            "examples": [],
            "confirmed": [],
            "inferred": [],
            "knowledge_points": [
                {
                    "title": "Limits",
                    "description": "Limit definition",
                    "prerequisites": [],
                    "importance": 5,
                    "expected_mastery_level": 2,
                    "confidence": "confirmed",
                    "sources": [],
                },
                {
                    "title": "Derivatives",
                    "description": "Derivative definition",
                    "prerequisites": ["Limits"],
                    "importance": 5,
                    "expected_mastery_level": 2,
                    "confidence": "confirmed",
                    "sources": [],
                },
            ],
            "learning_path": [],
        },
    )

    dependencies = database.list_dependencies(course["id"])
    debts = {item["title"]: item for item in database.list_debts(session["id"])}

    assert dependencies[0]["knowledge_point_title"] == "Derivatives"
    assert dependencies[0]["prerequisite_title"] == "Limits"
    assert debts["Limits"]["blocks_next_session"] is True
    assert debts["Derivatives"]["blocks_next_session"] is False

    database.update_point_mastery(debts["Limits"]["knowledge_point_id"], 2, "mastered")
    updated = {item["title"]: item for item in database.list_debts(session["id"])}
    assert updated["Limits"]["blocks_next_session"] is False
