from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


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


class MasteryEvidenceType(StrEnum):
    RECALL = "recall"
    UNDERSTANDING = "understanding"
    APPLICATION = "application"
    TRANSFER = "transfer"


class JobKind(StrEnum):
    ANALYSIS = "analysis"
    ASSESSMENT = "assessment"
    TRANSCRIPTION = "transcription"
    INDEXING = "indexing"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


DEFAULT_PROFILE = {
    "classroom": 40.0,
    "official_session": 35.0,
    "course_context": 15.0,
    "supplementary": 10.0,
}


class LocatorType(StrEnum):
    TRANSCRIPT = "transcript"
    PAGE = "page"
    SLIDE = "slide"
    CHUNK = "chunk"
    URL = "url"


class CourseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    semester: str = ""
    teacher: str | None = None
    schedule: str | None = None
    profile: dict[str, float] = Field(default_factory=lambda: DEFAULT_PROFILE.copy())

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value: dict[str, float]) -> dict[str, float]:
        if set(value) != set(DEFAULT_PROFILE):
            raise ValueError(f"profile must contain exactly these evidence channels: {', '.join(DEFAULT_PROFILE)}")
        if any(weight < 0 or weight > 100 for weight in value.values()) or abs(sum(value.values()) - 100) > 1e-6:
            raise ValueError("evidence channel weights must be between 0 and 100 and total 100")
        return value


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
        if not value or any(key not in DEFAULT_PROFILE for key in value):
            raise ValueError(f"profile keys must be evidence channels: {', '.join(DEFAULT_PROFILE)}")
        if any(weight < 0 or weight > 100 for weight in value.values()):
            raise ValueError("profile weights must be between 0 and 100")
        return value


class SourceRef(BaseModel):
    resource_id: str
    label: str
    locator: str | None = None
    locator_type: LocatorType | None = None
    start_time: float | None = Field(default=None, ge=0)
    end_time: float | None = Field(default=None, ge=0)
    page: int | None = Field(default=None, ge=1)
    slide: int | None = Field(default=None, ge=1)
    chunk_id: str | None = None

    @model_validator(mode="after")
    def validate_locator_shape(self) -> SourceRef:
        if self.locator_type == LocatorType.TRANSCRIPT:
            if self.start_time is None or self.end_time is None or self.end_time <= self.start_time:
                raise ValueError("transcript locators require an increasing start_time and end_time")
        elif self.locator_type == LocatorType.PAGE and self.page is None:
            raise ValueError("page locators require page")
        elif self.locator_type == LocatorType.SLIDE and self.slide is None:
            raise ValueError("slide locators require slide")
        elif self.locator_type == LocatorType.CHUNK and not self.chunk_id:
            raise ValueError("chunk locators require chunk_id")
        return self


class TranscriptSegment(BaseModel):
    id: str | None = None
    resource_id: str | None = None
    start_time: float = 0
    end_time: float = 0
    global_start: float | None = None
    global_end: float | None = None
    text: str

    @model_validator(mode="after")
    def validate_times(self) -> TranscriptSegment:
        if self.end_time < self.start_time:
            raise ValueError("transcript end_time must be after start_time")
        if self.global_start is not None and self.global_end is not None and self.global_end < self.global_start:
            raise ValueError("transcript global_end must be after global_start")
        return self


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
    knowledge_point_titles: list[str] = Field(min_length=1)
    prompt: str
    level: str
    question_type: str = "diagnostic"
    expected_mastery_level: int = Field(ge=1, le=4)
    reference_answer: str
    rubric: list[str]
    source_refs: list[SourceRef] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_single_point(cls, value: Any) -> Any:
        if isinstance(value, dict) and "knowledge_point_titles" not in value and value.get("knowledge_point_title"):
            value = {**value, "knowledge_point_titles": [value["knowledge_point_title"]]}
        return value


class KnowledgePointEvaluation(BaseModel):
    knowledge_point_title: str
    score: float = Field(ge=0, le=1)
    evidence_type: MasteryEvidenceType
    feedback: str = ""


class EvaluationResult(BaseModel):
    score: float = Field(ge=0, le=1)
    verdict: str
    met_criteria: list[str] = Field(default_factory=list)
    missing_criteria: list[str] = Field(default_factory=list)
    feedback: str
    point_results: list[KnowledgePointEvaluation] = Field(default_factory=list)


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


class JobCreate(BaseModel):
    kind: JobKind
    resource_id: str | None = None
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
