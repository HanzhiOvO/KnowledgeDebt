from app.models import DebtStatus
from app.scoring import debt_status, learning_coverage, reconstruction_score, update_mastery


def test_reconstruction_uses_effective_evidence_and_does_not_stack_duplicates():
    profile = {"audio": 35, "slides": 25, "textbook": 20}
    resources = [
        {"type": "audio", "coverage": 0.2, "quality": 0.5, "relevance": 1.0},
        {"type": "audio", "coverage": 0.1, "quality": 1.0, "relevance": 1.0},
        {"type": "slides", "coverage": 1.0, "quality": 0.9, "relevance": 1.0},
    ]
    assert reconstruction_score(resources, profile) == 26


def test_learning_coverage_is_independent_and_saturating():
    resources = [
        {"type": "textbook", "coverage": 1, "quality": 1, "relevance": 1},
        {"type": "slides", "coverage": 1, "quality": 1, "relevance": 1},
    ]
    assert learning_coverage(resources) == 98


def test_mastery_and_debt_status_respect_target_level():
    assert update_mastery(0, 0.9, 2) == 2
    assert debt_status(2, 2) == DebtStatus.MASTERED
    assert debt_status(1.2, 3) == DebtStatus.PARTIAL
    assert debt_status(0, 3, attempted=False) == DebtStatus.UNSEEN
