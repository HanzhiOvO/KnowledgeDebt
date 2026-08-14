from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class EvidenceLevel(StrEnum):
    CLASSROOM = "classroom"
    OFFICIAL = "official"
    SUPPLEMENTARY = "supplementary"


class ResourceType(StrEnum):
    AUDIO = "audio"
    VIDEO = "video"
    SLIDES = "slides"
    TEXTBOOK = "textbook"
    SYLLABUS = "syllabus"
    ASSIGNMENT = "assignment"
    NOTE = "note"
    LINK = "link"
    OTHER = "other"


class Confidence(StrEnum):
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    SUPPLEMENTARY = "supplementary"


class DebtStatus(StrEnum):
    UNSEEN = "unseen"
    UNMASTERED = "unmastered"
    PARTIAL = "partial"
    MASTERED = "mastered"


DEFAULT_PROFILE = {
    "audio": 35.0,
    "video": 35.0,
    "slides": 25.0,
    "textbook": 20.0,
    "assignment": 5.0,
    "syllabus": 5.0,
    "history": 5.0,
    "link": 5.0,
    "note": 15.0,
    "other": 5.0,
}


class CourseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    semester: str = ""
    teacher: str | None = None
    schedule: str | None = None
    profile: dict[str, float] = Field(default_factory=lambda: DEFAULT_PROFILE.copy())


class SessionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    starts_at: str | None = None
    ends_at: str | None = None
    notes: str = ""


class CourseProfileUpdate(BaseModel):
    profile: dict[str, float]

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value: dict[str, float]) -> dict[str, float]:
        if not value or any(weight < 0 or weight > 100 for weight in value.values()):
            raise ValueError("profile weights must be between 0 and 100")
        return value


class SourceRef(BaseModel):
    resource_id: str
    label: str
    locator: str | None = None


class TranscriptSegment(BaseModel):
    start_time: float = 0
    end_time: float = 0
    text: str


class TimelineItem(BaseModel):
    start_time: float | None = None
    end_time: float | None = None
    title: str
    summary: str
    confidence: Confidence
    sources: list[SourceRef] = Field(default_factory=list)


class KnowledgePointDraft(BaseModel):
    title: str
    description: str
    prerequisites: list[str] = Field(default_factory=list)
    importance: int = Field(default=3, ge=1, le=5)
    expected_mastery_level: int = Field(default=2, ge=1, le=4)
    confidence: Confidence = Confidence.INFERRED
    sources: list[SourceRef] = Field(default_factory=list)


class LearningStepDraft(BaseModel):
    position: int
    title: str
    brief_explanation: str
    full_explanation: str
    knowledge_point_titles: list[str] = Field(default_factory=list)
    estimated_minutes: int = Field(default=5, ge=1, le=90)
    confidence: Confidence = Confidence.SUPPLEMENTARY
    sources: list[SourceRef] = Field(default_factory=list)


class ReconstructionDraft(BaseModel):
    title: str
    summary: str
    topics: list[str] = Field(default_factory=list)
    timeline: list[TimelineItem] = Field(default_factory=list)
    teacher_emphasis: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    confirmed: list[str] = Field(default_factory=list)
    inferred: list[str] = Field(default_factory=list)
    knowledge_points: list[KnowledgePointDraft] = Field(default_factory=list)
    learning_path: list[LearningStepDraft] = Field(default_factory=list)


class QuestionDraft(BaseModel):
    knowledge_point_title: str
    prompt: str
    level: str
    expected_mastery_level: int = Field(ge=1, le=4)
    reference_answer: str
    rubric: list[str]
    source_refs: list[SourceRef] = Field(default_factory=list)


class EvaluationResult(BaseModel):
    score: float = Field(ge=0, le=1)
    verdict: str
    met_criteria: list[str] = Field(default_factory=list)
    missing_criteria: list[str] = Field(default_factory=list)
    feedback: str


class AnswerSubmission(BaseModel):
    answer: str = Field(min_length=1)
    confirm_external_upload: bool = False


class RemediationRequest(BaseModel):
    reason: str = "I did not understand. Explain it more simply."
    confirm_external_upload: bool = False


class RemediationDraft(BaseModel):
    knowledge_point_title: str
    diagnosis: str
    simpler_explanation: str
    analogy: str
    worked_example: str
    quick_check: str
    sources: list[SourceRef] = Field(default_factory=list)


class AnalysisRequest(BaseModel):
    confirm_external_upload: bool = False


class TranscriptionRequest(BaseModel):
    confirm_external_upload: bool = False


class ResourceQualityUpdate(BaseModel):
    coverage: float = Field(ge=0, le=1)
    quality: float = Field(ge=0, le=1)
    relevance: float = Field(ge=0, le=1)


class JsonRecord(BaseModel):
    id: str
    created_at: str
    updated_at: str
    data: dict[str, Any]
