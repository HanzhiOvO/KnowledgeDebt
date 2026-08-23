from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

from .provider_headers import validate_custom_headers


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


class TranscriptionState(StrEnum):
    SAVING = "saving"
    SAVED = "saved"
    PREPARING = "preparing"
    AWAITING_CONSENT = "awaiting_consent"
    AWAITING_CONFIGURATION = "awaiting_configuration"
    QUEUED = "queued"
    TRANSCRIBING = "transcribing"
    PARTIAL = "partial"
    TRANSCRIBED = "transcribed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProviderCapability(StrEnum):
    STRUCTURED_GENERATION = "structured_generation"
    CHAT_ANALYSIS = "chat_analysis"
    EMBEDDINGS = "embeddings"
    AUDIO_TRANSCRIPTION = "audio_transcription"
    ASYNC_AUDIO_TRANSCRIPTION = "async_audio_transcription"
    SEGMENT_TIMESTAMPS = "segment_timestamps"
    SPEAKER_DIARIZATION = "speaker_diarization"
    LONG_AUDIO = "long_audio"
    HOTWORDS = "hotwords"


class ProviderGroup(StrEnum):
    AI = "ai"
    ASR = "asr"
    EMBEDDING = "embedding"


class ReviewKind(StrEnum):
    ARCHIVE_MATCH = "archive_match"
    SESSION_TOPIC = "session_topic"
    SCHEDULE_CONFLICT = "schedule_conflict"
    TRANSCRIPTION_FAILURE = "transcription_failure"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    LATER = "later"


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
            raise ValueError(f"证据配置必须完整包含这些通道：{', '.join(DEFAULT_PROFILE)}")
        if any(weight < 0 or weight > 100 for weight in value.values()) or abs(sum(value.values()) - 100) > 1e-6:
            raise ValueError("证据通道权重必须在 0 到 100 之间，且合计为 100。")
        return value


class SessionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    starts_at: str | None = None
    ends_at: str | None = None
    notes: str = ""


class ProviderProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    vendor: str = Field(min_length=1, max_length=80)
    adapter: str = Field(default="openai_compatible", min_length=1, max_length=80)
    base_url: str = Field(default="", max_length=1000)
    region: str | None = Field(default=None, max_length=120)
    credential: str | None = Field(default=None, min_length=1, max_length=10000)
    credential_reference: str | None = Field(default=None, max_length=240)
    default_model: str = Field(default="", max_length=240)
    capabilities: list[ProviderCapability] = Field(default_factory=list)
    custom_headers: dict[str, str] = Field(default_factory=dict)
    external: bool = True
    enabled: bool = True

    @field_validator("custom_headers")
    @classmethod
    def validate_headers(cls, value: dict[str, str]) -> dict[str, str]:
        return validate_custom_headers(value)

    @model_validator(mode="after")
    def validate_credential_source(self) -> ProviderProfileCreate:
        if self.credential and self.credential_reference:
            raise ValueError("直接输入密钥和环境变量引用不能同时填写。")
        return self


class ProviderProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, max_length=1000)
    region: str | None = Field(default=None, max_length=120)
    credential: str | None = Field(default=None, min_length=1, max_length=10000)
    credential_reference: str | None = Field(default=None, max_length=240)
    default_model: str | None = Field(default=None, max_length=240)
    capabilities: list[ProviderCapability] | None = None
    custom_headers: dict[str, str] | None = None
    external: bool | None = None
    enabled: bool | None = None

    @field_validator("custom_headers")
    @classmethod
    def validate_headers(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return validate_custom_headers(value) if value is not None else None

    @model_validator(mode="after")
    def validate_credential_source(self) -> ProviderProfileUpdate:
        if self.credential and self.credential_reference:
            raise ValueError("直接输入密钥和环境变量引用不能同时填写。")
        return self


class ProviderDefaultUpdate(BaseModel):
    profile_id: str


class LocalModelDownloadRequest(BaseModel):
    confirmed: bool = False


class LocalModelDeleteRequest(BaseModel):
    confirmed: bool = False


class AcademicTermCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    starts_on: str
    ends_on: str
    timezone: str = "Asia/Shanghai"
    current: bool = True


class ScheduleConnectionUpdate(BaseModel):
    connector: str = "zjsu_undergraduate_v9"
    display_name: str = "浙江工商大学本科教务系统"
    sync_interval_minutes: int = Field(default=360, ge=30, le=10080)


class ScheduleRuleCreate(BaseModel):
    term_id: str
    course_name: str = Field(min_length=1, max_length=160)
    course_code: str | None = Field(default=None, max_length=80)
    class_name: str | None = Field(default=None, max_length=160)
    teacher: str | None = Field(default=None, max_length=160)
    campus: str | None = Field(default=None, max_length=160)
    building: str | None = Field(default=None, max_length=160)
    room: str | None = Field(default=None, max_length=160)
    weekday: int = Field(ge=1, le=7)
    start_period: int = Field(ge=1, le=30)
    end_period: int = Field(ge=1, le=30)
    weeks: list[int] = Field(min_length=1)
    odd_even: str = "all"
    notes: str = ""
    external_id: str = Field(min_length=1, max_length=240)
    aliases: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_periods_and_weeks(self) -> ScheduleRuleCreate:
        if self.end_period < self.start_period:
            raise ValueError("结束节次不能早于开始节次。")
        if any(week < 1 or week > 60 for week in self.weeks):
            raise ValueError("周次必须在 1 到 60 之间。")
        if self.odd_even not in {"all", "odd", "even"}:
            raise ValueError("单双周规则只支持 all、odd 或 even。")
        return self


class OccurrenceMaterializeRequest(BaseModel):
    reason: str = Field(default="opened", pattern="^(occurred|evidence|opened)$")


class InboxDecision(BaseModel):
    session_id: str | None = None
    reason: str = "user_review"


class ReviewDecision(BaseModel):
    action: str = Field(pattern="^(accept|edit_accept|reject|later|pending)$")
    edited_value: str | None = Field(default=None, max_length=500)
    reason: str = Field(default="", max_length=1000)
    snoozed_until: str | None = None


class AppSettingsUpdate(BaseModel):
    timezone: str | None = Field(default=None, min_length=1, max_length=120)
    auto_transcribe: bool | None = None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("时区必须是有效的 IANA 时区，例如 Asia/Shanghai。") from exc
        return value


class RecordingCreate(BaseModel):
    recording_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    mime_type: str = Field(min_length=1, max_length=160)
    filename: str = Field(min_length=1, max_length=240)
    start_offset: float = Field(default=0, ge=0)
    session_duration: float | None = Field(default=None, gt=0)
    auto_transcribe: bool | None = None


class RecordingFinalize(BaseModel):
    last_sequence: int = Field(ge=0, le=100_000)
    duration_seconds: float = Field(gt=0)


class SessionTitleUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    locked: bool = True


class CourseProfileUpdate(BaseModel):
    profile: dict[str, float]

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value: dict[str, float]) -> dict[str, float]:
        if not value or any(key not in DEFAULT_PROFILE for key in value):
            raise ValueError(f"证据配置只能使用这些通道：{', '.join(DEFAULT_PROFILE)}")
        if any(weight < 0 or weight > 100 for weight in value.values()):
            raise ValueError("证据通道权重必须在 0 到 100 之间。")
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
                raise ValueError("转写引用必须包含递增的开始和结束时间。")
        elif self.locator_type == LocatorType.PAGE and self.page is None:
            raise ValueError("PDF 引用必须包含页码。")
        elif self.locator_type == LocatorType.SLIDE and self.slide is None:
            raise ValueError("课件引用必须包含页序号。")
        elif self.locator_type == LocatorType.CHUNK and not self.chunk_id:
            raise ValueError("文档引用必须包含 chunk_id。")
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
            raise ValueError("转写结束时间必须晚于开始时间。")
        if self.global_start is not None and self.global_end is not None and self.global_end < self.global_start:
            raise ValueError("转写全局结束时间必须晚于全局开始时间。")
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
