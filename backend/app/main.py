from __future__ import annotations

import re
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import Settings
from .database import Database
from .documents import extract_text
from .models import (
    AnalysisRequest,
    AnswerSubmission,
    CourseCreate,
    CourseProfileUpdate,
    EvidenceLevel,
    RemediationRequest,
    ResourceQualityUpdate,
    ResourceType,
    SessionCreate,
    TranscriptionRequest,
)
from .providers.base import (
    AIProvider,
    ProviderNotConfigured,
    ProviderOutputError,
    ProviderRequestError,
    TranscriptionProvider,
)
from .providers.openai_compatible import OpenAICompatibleProvider
from .scoring import minimum_daily_minutes
from .service import KnowledgeService


class LinkResourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=4, max_length=2000)
    evidence_level: EvidenceLevel = EvidenceLevel.SUPPLEMENTARY
    resource_type: ResourceType = ResourceType.LINK
    notes: str = ""


def _safe_name(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]", "_", Path(name).name)
    return clean[:180] or "resource"


def _permission(confirmed: bool, provider: object) -> None:
    if getattr(provider, "requires_external_upload", False) and not confirmed:
        raise HTTPException(
            status_code=409,
            detail="This action sends selected session material to the configured provider. Explicit confirmation is required.",
        )


def create_app(
    settings: Settings | None = None,
    db: Database | None = None,
    ai_provider: AIProvider | None = None,
    asr_provider: TranscriptionProvider | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    database = db or Database(settings.data_dir / "knowledgedebt.sqlite3")
    default_provider = OpenAICompatibleProvider(
        settings.api_key, settings.base_url, settings.ai_model, settings.asr_model
    )
    service = KnowledgeService(database, ai_provider or default_provider, asr_provider or default_provider)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield

    app = FastAPI(
        title="KnowledgeDebt API",
        version="0.1.0",
        description="Local-first course reconstruction and mastery assessment API",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = database
    app.state.service = service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost", "http://127.0.0.1"],
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(KeyError)
    async def missing_handler(_, exc: KeyError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=404, content={"detail": f"{exc.args[0]} not found"})

    @app.exception_handler(ProviderNotConfigured)
    async def provider_handler(_, exc: ProviderNotConfigured):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(ProviderRequestError)
    @app.exception_handler(ProviderOutputError)
    async def provider_failure_handler(_, exc: RuntimeError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/settings/provider")
    def provider_settings() -> dict:
        return {
            "ai_provider": settings.ai_provider,
            "asr_provider": settings.asr_provider,
            "ai_model": settings.ai_model,
            "asr_model": settings.asr_model,
            "configured": bool(settings.api_key),
            "external_upload_requires_confirmation": True,
        }

    @app.post("/courses", status_code=201)
    def create_course(payload: CourseCreate) -> dict:
        return database.create_course(payload)

    @app.get("/courses")
    def list_courses() -> list[dict]:
        return database.list_courses()

    @app.get("/courses/{course_id}")
    def get_course(course_id: str) -> dict:
        course = database.get_course(course_id)
        course["sessions"] = database.list_sessions(course_id)
        return course

    @app.patch("/courses/{course_id}/profile")
    def update_course_profile(course_id: str, payload: CourseProfileUpdate) -> dict:
        return database.update_course_profile(course_id, payload.profile)

    @app.post("/courses/{course_id}/sessions", status_code=201)
    def create_session(course_id: str, payload: SessionCreate) -> dict:
        return database.create_session(course_id, payload)

    @app.get("/sessions")
    def list_sessions(course_id: str | None = None) -> list[dict]:
        return database.list_sessions(course_id)

    @app.get("/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        return database.get_session(session_id)

    @app.post("/sessions/{session_id}/resources/upload", status_code=201)
    async def upload_resource(
        session_id: str,
        file: UploadFile = File(...),
        resource_type: ResourceType = Form(...),
        evidence_level: EvidenceLevel = Form(...),
        coverage: float = Form(1.0),
        quality: float = Form(1.0),
        relevance: float = Form(1.0),
        duration_seconds: float | None = Form(None),
    ) -> dict:
        if not all(0 <= value <= 1 for value in (coverage, quality, relevance)):
            raise HTTPException(status_code=422, detail="coverage, quality and relevance must be between 0 and 1")
        directory = settings.data_dir / "resources" / session_id
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{uuid.uuid4().hex}_{_safe_name(file.filename or 'resource')}"
        with target.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
        extracted = extract_text(target, file.content_type)
        if (
            resource_type
            in {
                ResourceType.SLIDES,
                ResourceType.TEXTBOOK,
                ResourceType.SYLLABUS,
                ResourceType.ASSIGNMENT,
                ResourceType.NOTE,
            }
            and not extracted.strip()
        ):
            quality = min(quality, 0.2)
        resource = database.add_resource(
            session_id,
            type=resource_type.value,
            evidence_level=evidence_level.value,
            name=file.filename or target.name,
            mime_type=file.content_type,
            local_path=str(target),
            extracted_text=extracted,
            coverage=coverage,
            quality=quality,
            relevance=relevance,
            duration_seconds=duration_seconds,
        )
        reconstruction, learning = service.refresh_scores(session_id)
        resource["session_scores"] = {"reconstruction": reconstruction, "learning_coverage": learning}
        return resource

    @app.post("/sessions/{session_id}/resources/link", status_code=201)
    def add_link_resource(session_id: str, payload: LinkResourceCreate) -> dict:
        resource = database.add_resource(
            session_id,
            type=payload.resource_type.value,
            evidence_level=payload.evidence_level.value,
            name=payload.name,
            external_url=payload.url,
            extracted_text=payload.notes,
        )
        service.refresh_scores(session_id)
        return resource

    @app.patch("/resources/{resource_id}/quality")
    def update_resource_quality(resource_id: str, payload: ResourceQualityUpdate) -> dict:
        resource = database.update_resource_quality(resource_id, payload.coverage, payload.quality, payload.relevance)
        service.refresh_scores(resource["session_id"])
        return resource

    @app.post("/resources/{resource_id}/transcribe")
    async def transcribe(resource_id: str, payload: TranscriptionRequest) -> dict:
        resource = database.get_resource(resource_id)
        if resource["type"] not in {"audio", "video"} or not resource["local_path"]:
            raise HTTPException(
                status_code=422, detail="Only a locally stored audio or video resource can be transcribed"
            )
        _permission(payload.confirm_external_upload, service.asr)
        segments = await service.asr.transcribe(resource["local_path"], resource["mime_type"])
        database.save_transcript(resource_id, [item.model_dump() for item in segments])
        service.refresh_scores(resource["session_id"])
        return {"resource_id": resource_id, "segments": [item.model_dump() for item in segments]}

    @app.post("/sessions/{session_id}/analyze")
    async def analyze(session_id: str, payload: AnalysisRequest) -> dict:
        _permission(payload.confirm_external_upload, service.ai)
        return await service.analyze(session_id)

    @app.post("/sessions/{session_id}/assessment", status_code=201)
    async def generate_assessment(session_id: str, payload: AnalysisRequest) -> list[dict]:
        _permission(payload.confirm_external_upload, service.ai)
        questions = await service.make_quiz(session_id)
        for question in questions:
            question.pop("reference_answer", None)
            question.pop("rubric", None)
        return questions

    @app.get("/sessions/{session_id}/assessment")
    def list_assessment(session_id: str) -> list[dict]:
        questions = database.list_questions(session_id)
        for question in questions:
            question.pop("reference_answer", None)
            question.pop("rubric", None)
        return questions

    @app.post("/questions/{question_id}/answer")
    async def answer(question_id: str, payload: AnswerSubmission) -> dict:
        _permission(payload.confirm_external_upload, service.ai)
        return await service.evaluate(question_id, payload.answer)

    @app.post("/knowledge-points/{point_id}/remediation", status_code=201)
    async def remediate(point_id: str, payload: RemediationRequest) -> dict:
        _permission(payload.confirm_external_upload, service.ai)
        return await service.remediate(point_id, payload.reason)

    @app.post("/learning-steps/{step_id}/complete")
    def complete_learning_step(step_id: str) -> dict:
        database.complete_learning_step(step_id)
        return {"id": step_id, "completed": True}

    @app.get("/debts")
    def debts() -> list[dict]:
        return database.list_debts()

    @app.get("/home")
    def home() -> dict:
        courses = {course["id"]: course for course in database.list_courses()}
        sessions = database.list_sessions()
        all_debts = database.list_debts()
        debts_by_session: dict[str, list[dict]] = {}
        for debt in all_debts:
            debts_by_session.setdefault(debt["session_id"], []).append(debt)
        session_cards = []
        for session in sessions:
            session_debts = debts_by_session.get(session["id"], [])
            session_cards.append(
                {
                    **session,
                    "course_name": courses[session["course_id"]]["name"],
                    "open_debt_count": sum(item["status"] != "mastered" for item in session_debts),
                }
            )
        open_debts = [item for item in all_debts if item["status"] != "mastered"]
        pending_sessions = [item for item in sessions if item["status"] != "complete"]
        unanalyzed_sessions = [item for item in pending_sessions if not debts_by_session.get(item["id"])]
        return {
            "sessions": session_cards,
            "open_debt_count": len(open_debts),
            "urgent_debt_count": sum(item["priority"] >= 4 for item in open_debts),
            "pending_session_count": len(pending_sessions),
            "minimum_minutes": minimum_daily_minutes(open_debts) + len(unanalyzed_sessions) * 5,
        }

    return app


app = create_app()
