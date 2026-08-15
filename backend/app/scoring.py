from __future__ import annotations

from collections.abc import Iterable

from .models import DEFAULT_PROFILE, DebtStatus

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

MASTERY_EVIDENCE_WEIGHTS = {
    "recall": 0.75,
    "understanding": 1.0,
    "application": 1.2,
    "transfer": 1.4,
}


def _bounded(value: object, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def evidence_channel(resource: dict) -> str:
    """Map a resource to one reconstruction channel without double counting its type."""
    level = resource.get("evidence_level")
    kind = resource.get("type")
    if level == "classroom":
        return "classroom"
    if level == "official" and kind in {"slides", "assignment", "note", "audio", "video"}:
        return "official_session"
    if level == "official":
        return "course_context"
    return "supplementary"


def recording_union_effective(resources: Iterable[dict]) -> float:
    """Integrate the best recording quality over the union of Session time ranges.

    Overlap is counted once. Quality and relevance are applied to the interval that
    supplied them, so three high-quality chunks covering 80 of 100 minutes produce
    0.8 rather than three independent full-audio contributions.
    """
    recordings = list(resources)
    session_duration = max(
        (float(item.get("session_duration") or 0) for item in recordings),
        default=0.0,
    )
    ranges: list[tuple[float, float, float]] = []
    for item in recordings:
        start = item.get("start_offset")
        end = item.get("end_offset")
        if start is None or end is None:
            continue
        start_value, end_value = float(start), float(end)
        if session_duration <= 0 or start_value < 0 or end_value <= start_value:
            continue
        end_value = min(end_value, session_duration)
        if end_value <= start_value:
            continue
        confidence = _bounded(item.get("quality"), 1.0) * _bounded(item.get("relevance"), 1.0)
        ranges.append((start_value, end_value, confidence))
    if not ranges or session_duration <= 0:
        return max(
            (
                _bounded(item.get("coverage"), 0.0)
                * _bounded(item.get("quality"), 1.0)
                * _bounded(item.get("relevance"), 1.0)
                for item in recordings
            ),
            default=0.0,
        )

    boundaries = sorted({point for start, end, _ in ranges for point in (start, end)})
    effective_seconds = 0.0
    for left, right in zip(boundaries, boundaries[1:], strict=False):
        if right <= left:
            continue
        confidence = max(
            (quality for start, end, quality in ranges if start < right and end > left),
            default=0.0,
        )
        effective_seconds += (right - left) * confidence
    return _bounded(effective_seconds / session_duration)


def _channel_effective(resources: list[dict]) -> float:
    factors: list[float] = []
    timed_recordings = [
        item
        for item in resources
        if item.get("type") in {"audio", "video"}
        and item.get("start_offset") is not None
        and item.get("end_offset") is not None
        and item.get("session_duration")
    ]
    if timed_recordings:
        factors.append(recording_union_effective(timed_recordings))

    for resource in resources:
        if resource in timed_recordings:
            continue
        factors.append(
            _bounded(resource.get("coverage"), 0.0)
            * _bounded(resource.get("quality"), 1.0)
            * _bounded(resource.get("relevance"), 1.0)
        )

    remaining_gap = 1.0
    for factor in factors:
        remaining_gap *= 1.0 - _bounded(factor)
    return 1.0 - remaining_gap


def reconstruction_score(resources: Iterable[dict], profile: dict[str, float]) -> int:
    """Score four bounded evidence channels whose configured weights total 100."""
    grouped = {channel: [] for channel in DEFAULT_PROFILE}
    for resource in resources:
        grouped[evidence_channel(resource)].append(resource)
    weights = profile if set(DEFAULT_PROFILE).issubset(profile) else DEFAULT_PROFILE
    total_weight = sum(max(0.0, float(weights.get(channel, 0.0))) for channel in DEFAULT_PROFILE)
    if total_weight <= 0:
        return 0
    raw = sum(
        (max(0.0, float(weights.get(channel, 0.0))) / total_weight)
        * _channel_effective(grouped[channel])
        for channel in DEFAULT_PROFILE
    )
    return round(_bounded(raw) * 100)


def learning_coverage(resources: Iterable[dict]) -> int:
    """Independent probability-style coverage of whether the session can be re-taught."""
    remaining_gap = 1.0
    for resource in resources:
        utility = LEARNING_UTILITY.get(resource["type"], LEARNING_UTILITY["other"])
        if resource.get("type") in {"audio", "video"} and resource.get("session_duration"):
            effective = min(0.92, utility * recording_union_effective([resource]))
        else:
            effective = min(
                0.92,
                utility
                * _bounded(resource.get("coverage"), 0.0)
                * _bounded(resource.get("quality"), 1.0)
                * _bounded(resource.get("relevance"), 1.0),
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
    return round(max(0.0, min(4.0, updated)), 2)


def aggregate_mastery(evidence: Iterable[dict], target: int) -> float:
    """Aggregate persisted evidence; one correct answer can never clear a debt."""
    items = list(evidence)
    if not items:
        return 0.0
    weighted_score = 0.0
    total_weight = 0.0
    for item in items:
        type_weight = MASTERY_EVIDENCE_WEIGHTS.get(item.get("evidence_type", "understanding"), 1.0)
        weight = max(0.0, float(item.get("weight", 1.0))) * type_weight
        weighted_score += _bounded(item.get("score")) * weight
        total_weight += weight
    ratio = weighted_score / total_weight if total_weight else 0.0
    mastery = ratio * target
    strong_types = {
        item.get("evidence_type")
        for item in items
        if _bounded(item.get("score")) >= 0.75
    }
    required_high_level = "recall" if target <= 1 else "understanding"
    eligible = len(items) >= 2 and (
        required_high_level in strong_types
        or "application" in strong_types
        or "transfer" in strong_types
    )
    if eligible and ratio >= 0.82:
        mastery = float(target)
    elif not eligible:
        mastery = min(mastery, max(0.0, target - 0.01))
    return round(max(0.0, min(4.0, mastery)), 2)


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
