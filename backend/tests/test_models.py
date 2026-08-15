from app.models import DEFAULT_PROFILE, CourseCreate, ReconstructionDraft


def test_course_profile_is_not_shared_between_models():
    first = CourseCreate(name="Calculus")
    second = CourseCreate(name="Physics")
    first.profile["classroom"] = 1
    assert second.profile["classroom"] == DEFAULT_PROFILE["classroom"]
    assert sum(DEFAULT_PROFILE.values()) == 100


def test_structured_analysis_round_trip():
    payload = {
        "title": "Mean value theorem",
        "summary": "A lesson",
        "topics": ["Rolle"],
        "timeline": [],
        "teacher_emphasis": [],
        "examples": [],
        "confirmed": [],
        "inferred": ["Proof may have been discussed"],
        "knowledge_points": [],
        "learning_path": [],
    }
    parsed = ReconstructionDraft.model_validate(payload)
    assert ReconstructionDraft.model_validate_json(parsed.model_dump_json()) == parsed
