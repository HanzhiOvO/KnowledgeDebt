from app.models import DEFAULT_PROFILE, DebtStatus
from app.scoring import (
    debt_status,
    learning_coverage,
    reconstruction_score,
    recording_union_effective,
    update_mastery,
)


def test_reconstruction_uses_bounded_evidence_channels():
    resources = [
        {"type": "audio", "evidence_level": "classroom", "coverage": 1, "quality": 1, "relevance": 1},
        {"type": "video", "evidence_level": "classroom", "coverage": 1, "quality": 1, "relevance": 1},
        {"type": "slides", "evidence_level": "official", "coverage": 1, "quality": 1, "relevance": 1},
    ]
    assert reconstruction_score(resources, DEFAULT_PROFILE) == 75


def test_recording_chunks_use_union_coverage_with_quality():
    recordings = [
        {"start_offset": 0, "end_offset": 1200, "session_duration": 6000, "quality": 1, "relevance": 1},
        {"start_offset": 1200, "end_offset": 2400, "session_duration": 6000, "quality": 1, "relevance": 1},
        {"start_offset": 3600, "end_offset": 6000, "session_duration": 6000, "quality": 1, "relevance": 1},
    ]
    assert recording_union_effective(recordings) == 0.8


def test_overlapping_recordings_do_not_double_count_and_apply_best_quality():
    recordings = [
        {"start_offset": 0, "end_offset": 60, "session_duration": 100, "quality": 0.5, "relevance": 1},
        {"start_offset": 40, "end_offset": 80, "session_duration": 100, "quality": 1, "relevance": 1},
    ]
    assert recording_union_effective(recordings) == 0.6


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
