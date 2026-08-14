from __future__ import annotations

from collections.abc import Iterable

from .models import DebtStatus

LEARNING_UTILITY = {
    "audio": 0.45,
    "video": 0.60,
    "slides": 0.75,
    "textbook": 0.95,
    "assignment": 0.65,
    "syllabus": 0.30,
    "note": 0.55,
    "link": 0.70,
    "other": 0.35,
}


def reconstruction_score(resources: Iterable[dict], profile: dict[str, float]) -> int:
    """Evidence-weighted score; duplicate resource types saturate instead of stacking."""
    best_by_type: dict[str, float] = {}
    for resource in resources:
        kind = resource["type"]
        contribution = (
            float(resource.get("coverage", 0.0))
            * float(resource.get("quality", 0.0))
            * float(resource.get("relevance", 0.0))
        )
        best_by_type[kind] = max(best_by_type.get(kind, 0.0), contribution)
    raw = sum(float(profile.get(kind, 0)) * factor for kind, factor in best_by_type.items())
    return max(0, min(100, round(raw)))


def learning_coverage(resources: Iterable[dict]) -> int:
    """Independent probability-style coverage of whether the session can be re-taught."""
    remaining_gap = 1.0
    for resource in resources:
        utility = LEARNING_UTILITY.get(resource["type"], LEARNING_UTILITY["other"])
        effective = min(
            0.92,
            utility
            * float(resource.get("coverage", 0.0))
            * float(resource.get("quality", 0.0))
            * float(resource.get("relevance", 0.0)),
        )
        remaining_gap *= 1.0 - effective
    return max(0, min(100, round((1.0 - remaining_gap) * 100)))


def debt_status(current: float, target: int, attempted: bool = True) -> DebtStatus:
    if current >= target:
        return DebtStatus.MASTERED
    if not attempted and current <= 0:
        return DebtStatus.UNSEEN
    if current + 1e-9 < max(0.75, target * 0.4):
        return DebtStatus.UNMASTERED
    return DebtStatus.PARTIAL


def update_mastery(current: float, assessment_score: float, target: int) -> float:
    """Recency-weighted update on the 0..4 mastery scale."""
    observed = max(0.0, min(1.0, assessment_score)) * target
    updated = current * 0.35 + observed * 0.65
    if assessment_score >= 0.88:
        updated = max(updated, float(target))
    return round(max(0.0, min(4.0, updated)), 2)


def minimum_daily_minutes(debts: Iterable[dict]) -> int:
    total = 0.0
    for debt in debts:
        if debt["status"] == DebtStatus.MASTERED.value:
            continue
        gap = max(0.0, float(debt["target_mastery"]) - float(debt["current_mastery"]))
        dependency_multiplier = 1.35 if debt.get("blocks_next_session") else 1.0
        priority_multiplier = 1.25 if debt.get("priority", 3) >= 4 else 1.0
        total += max(3.0, gap * 4.0) * dependency_multiplier * priority_multiplier
    return round(total)
