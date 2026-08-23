from __future__ import annotations

import re
import secrets
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace as replace_settings
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .config import Settings
from .database import Database
from .documents import extract_document
from .models import (
    AnalysisRequest,
    AnswerSubmission,
    CourseCreate,
    CourseProfileUpdate,
    EvidenceLevel,
    JobCreate,
    JobKind,
    JobStatus,
    RemediationRequest,
    ResourceQualityUpdate,
    ResourceType,
    SessionCreate,
    TranscriptionRequest,
)
from .providers.base import (
    AIProvider,
    EmbeddingProvider,
    ProviderNotConfigured,
    ProviderOutputError,
    ProviderRequestError,
    TranscriptionProvider,
)
from .providers.factory import build_ai_provider, build_asr_provider, build_embedding_provider
from .providers.local_whisper import LocalWhisperProvider
from .providers.presets import PROVIDER_PRESETS, normalize_provider, public_presets, resolve_preset
from .retrieval import RetrievalPolicy
from .runtime_settings import mask_api_key, save_runtime_provider
from .scoring import minimum_daily_minutes
from .service import KnowledgeService
from .storage import LocalStorageProvider, S3StorageProvider, StorageProvider, StoredObject


class LinkResourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=4, max_length=2000)
    evidence_level: EvidenceLevel = EvidenceLevel.SUPPLEMENTARY
    resource_type: ResourceType = ResourceType.LINK
    notes: str = ""


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    policy: RetrievalPolicy = RetrievalPolicy.RECONSTRUCTION
    limit: int = Field(default=12, ge=1, le=50)


class ModelProviderUpdate(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    api_key: str | None = Field(default=None, max_length=512)
    model: str | None = Field(default=None, max_length=120)
    base_url: str | None = Field(default=None, max_length=500)


def _safe_name(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]", "_", Path(name).name)
    return clean[:180] or "resource"


PRIVATE_RESOURCE_FIELDS = {"local_path", "storage_key"}
PRIVATE_CHUNK_FIELDS = {"embedding", "metadata", "visual_path"}


def _public_chunk(chunk: dict) -> dict:
    return {key: value for key, value in chunk.items() if key not in PRIVATE_CHUNK_FIELDS}


def _public_resource(resource: dict) -> dict:
    result = {key: value for key, value in resource.items() if key not in PRIVATE_RESOURCE_FIELDS}
    result["chunks"] = [_public_chunk(chunk) for chunk in resource.get("chunks", [])]
    return result


def _public_session(session: dict) -> dict:
    result = dict(session)
    result["resources"] = [_public_resource(resource) for resource in session.get("resources", [])]
    return result


def _local_asr_prompt(session: dict, resource: dict) -> str:
    """Build a small glossary prompt so Whisper spells course terms correctly."""
    glossary: list[str] = [str(session.get("title") or "")]
    notes = str(session.get("notes") or "").strip()
    if notes:
        glossary.append(notes[:300])
    for item in session.get("resources", []):
        if item.get("id") == resource.get("id") or item.get("type") in {"audio", "video"}:
            continue
        text = " ".join(str(item.get("extracted_text") or "").split())[:300]
        if text:
            glossary.append(text)
        elif item.get("name"):
            glossary.append(str(item["name"]))
    prompt = "；".join(part for part in glossary if part)[:1200]
    return f"课程主题与术语提示：{prompt}；当前转写资料：{resource.get('name', '')}"


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
    embedding_provider: EmbeddingProvider | None = None,
    storage_provider: StorageProvider | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    database = db or Database(settings.database_url or settings.data_dir / "knowledgedebt.sqlite3")
    selected_ai_provider = ai_provider or build_ai_provider(settings)
    selected_asr_provider = asr_provider or build_asr_provider(settings)
    selected_embedding_provider = embedding_provider or build_embedding_provider(settings)
    service = KnowledgeService(
        database,
        selected_ai_provider,
        selected_asr_provider,
        selected_embedding_provider,
    )
    if storage_provider:
        storage = storage_provider
    elif settings.storage_provider == "s3":
        if not settings.s3_bucket:
            raise ValueError("KNOWLEDGEDEBT_S3_BUCKET is required for S3 storage")
        storage = S3StorageProvider(settings.s3_bucket, settings.s3_endpoint_url)
    else:
        storage = LocalStorageProvider(settings.data_dir / "resources")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield

    app = FastAPI(
        title="知债 KnowledgeDebt API",
        version="0.2.0",
        description="Local-first course reconstruction and mastery assessment API",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = database
    app.state.service = service
    app.state.storage = storage

    def apply_model_provider(next_settings: Settings) -> None:
        nonlocal settings
        settings = next_settings
        app.state.settings = next_settings
        service.ai = build_ai_provider(next_settings)
        if next_settings.embedding_provider == "openai_compatible":
            service.embeddings = build_embedding_provider(next_settings)
            service.retriever.embeddings = service.embeddings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost", "http://127.0.0.1"],
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def optional_access_token(request: Request, call_next):
        """Protect the API when the operator configures a single-user token."""

        if not settings.access_token or request.method == "OPTIONS" or request.url.path == "/health":
            return await call_next(request)
        authorization = request.headers.get("authorization", "")
        scheme, _, supplied_token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(
            supplied_token, settings.access_token
        ):
            return JSONResponse(
                status_code=401,
                content={"detail": "A valid access token is required."},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)

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

    @app.exception_handler(ValueError)
    async def validation_handler(_, exc: ValueError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": "0.2.0"}

    def asr_configured() -> bool:
        if settings.asr_provider == "local_whisper":
            return LocalWhisperProvider.available()
        return bool(settings.api_key) and settings.asr_provider != "local_rule"

    @app.get("/settings/provider")
    def provider_settings() -> dict:
        preset = resolve_preset(settings.ai_provider)
        return {
            "ai_provider": settings.ai_provider,
            "ai_label": preset.label,
            "api_style": preset.api_style,
            "base_url": settings.base_url,
            "asr_provider": settings.asr_provider,
            "ai_model": settings.ai_model,
            "asr_model": settings.asr_model,
            "embedding_provider": settings.embedding_provider,
            "embedding_model": settings.embedding_model,
            "storage_provider": storage.name,
            "local_mode": settings.ai_provider == "local_rule",
            "configured": not getattr(service.ai, "requires_external_upload", False) or bool(settings.api_key),
            "asr_configured": asr_configured(),
            "local_asr_model": settings.local_asr_model if settings.asr_provider == "local_whisper" else None,
            "masked_api_key": mask_api_key(settings.api_key),
            "access_token_configured": bool(settings.access_token),
            "external_upload_requires_confirmation": (
                getattr(service.ai, "requires_external_upload", False)
                or getattr(service.embeddings, "requires_external_upload", False)
            ),
        }

    @app.get("/settings/providers")
    def model_provider_options() -> dict:
        return {"presets": public_presets(), "current": provider_settings()}

    @app.post("/settings/model-provider")
    def update_model_provider(payload: ModelProviderUpdate) -> dict:
        provider = normalize_provider(payload.provider)
        if not provider or provider not in PROVIDER_PRESETS:
            raise HTTPException(status_code=422, detail="unsupported model provider")
        preset = resolve_preset(provider)
        api_key = (payload.api_key or "").strip() or None
        if preset.key_required and not api_key:
            raise HTTPException(status_code=422, detail="请粘贴该 Provider 的官方 API Key")
        if provider == "local_rule":
            api_key = None
        base_url = (payload.base_url or "").strip() or preset.base_url
        model = (payload.model or "").strip() or preset.default_model
        save_runtime_provider(settings.data_dir, provider, api_key, base_url, model)
        next_settings = replace_settings(
            settings,
            ai_provider=provider,
            api_key=api_key,
            base_url=base_url,
            ai_model=model,
        )
        apply_model_provider(next_settings)
        return {"current": provider_settings(), "saved": True}

    @app.post("/settings/model-provider/local")
    def use_local_model_provider() -> dict:
        save_runtime_provider(settings.data_dir, "local_rule", None, "", "local")
        next_settings = replace_settings(
            settings,
            ai_provider="local_rule",
            api_key=None,
            base_url="",
            ai_model="local",
        )
        apply_model_provider(next_settings)
        return {"current": provider_settings(), "saved": True}

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
        return _public_session(database.create_session(course_id, payload))

    @app.get("/sessions")
    def list_sessions(course_id: str | None = None) -> list[dict]:
        return database.list_sessions(course_id)

    @app.get("/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        return _public_session(database.get_session(session_id))

    @app.get("/sessions/{session_id}/consent-manifest")
    def consent_manifest(session_id: str, operation: str, resource_id: str | None = None) -> dict:
        session = database.get_session(session_id)
        if operation not in {"analysis", "assessment", "remediation", "transcription", "indexing"}:
            raise HTTPException(status_code=422, detail="unsupported operation")
        providers = [(settings.ai_provider, service.ai), (settings.embedding_provider, service.embeddings)]
        resources = session["resources"]
        sends = ["Session title and notes", "retrieved transcript segments", "retrieved document chunks"]
        does_not_send = ["local filesystem paths", "unselected document chunks", "original audio/video binaries"]
        if operation == "transcription":
            providers = [(settings.asr_provider, service.asr)]
            resources = [item for item in resources if item["id"] == resource_id]
            if not resources:
                raise HTTPException(status_code=422, detail="select a Session audio or video resource")
            sends = ["the selected original audio/video binary", "its MIME type and filename"]
            does_not_send = ["other Session resources", "course history", "local filesystem paths"]
        elif operation == "indexing":
            providers = [(settings.embedding_provider, service.embeddings)]
            resources = [item for item in resources if not resource_id or item["id"] == resource_id]
            sends = ["text chunks from the listed resources"]
            does_not_send = ["original files", "audio/video binaries", "local filesystem paths"]
        external_providers = [
            name for name, provider in providers if getattr(provider, "requires_external_upload", False)
        ]
        return {
            "operation": operation,
            "provider": ", ".join(external_providers) if external_providers else "local providers",
            "providers": [name for name, _ in providers],
            "external": bool(external_providers),
            "resources": [
                {"id": item["id"], "name": item["name"], "type": item["type"]} for item in resources
            ],
            "will_send": sends,
            "will_not_send": does_not_send,
            "confirmation_required": bool(external_providers),
        }

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
        start_offset: float | None = Form(None),
        end_offset: float | None = Form(None),
        session_duration: float | None = Form(None),
    ) -> dict:
        if not all(0 <= value <= 1 for value in (coverage, quality, relevance)):
            raise HTTPException(status_code=422, detail="coverage, quality and relevance must be between 0 and 1")
        if duration_seconds is not None and duration_seconds <= 0:
            raise HTTPException(status_code=422, detail="duration_seconds must be positive")
        if start_offset is not None and start_offset < 0:
            raise HTTPException(status_code=422, detail="start_offset cannot be negative")
        if end_offset is None and start_offset is not None and duration_seconds is not None:
            end_offset = start_offset + duration_seconds
        if end_offset is not None and (start_offset is None or end_offset <= start_offset):
            raise HTTPException(status_code=422, detail="end_offset must be after start_offset")
        if session_duration is not None and session_duration <= 0:
            raise HTTPException(status_code=422, detail="session_duration must be positive")
        if session_duration is not None and end_offset is not None and end_offset > session_duration:
            raise HTTPException(status_code=422, detail="capture range cannot exceed session_duration")
        capture_range = [start_offset, end_offset] if start_offset is not None and end_offset is not None else []
        upload_id = uuid.uuid4().hex
        key = f"{session_id}/{upload_id}_{_safe_name(file.filename or 'resource')}"
        stored = storage.save(key, file.file, file.content_type)
        target = storage.materialize(stored)
        extraction = extract_document(target, file.content_type, settings.data_dir / "derived" / upload_id)
        if (
            resource_type
            in {
                ResourceType.SLIDES,
                ResourceType.TEXTBOOK,
                ResourceType.SYLLABUS,
                ResourceType.ASSIGNMENT,
                ResourceType.NOTE,
            }
            and not extraction.text.strip()
        ):
            quality = min(quality, 0.2)
        resource = database.add_resource(
            session_id,
            type=resource_type.value,
            evidence_level=evidence_level.value,
            name=file.filename or target.name,
            mime_type=file.content_type,
            local_path=str(target) if stored.local_path else None,
            storage_provider=stored.provider,
            storage_key=stored.key,
            extracted_text=extraction.text,
            coverage=coverage,
            quality=quality,
            relevance=relevance,
            duration_seconds=duration_seconds,
            start_offset=start_offset,
            end_offset=end_offset,
            session_duration=session_duration,
            capture_range=capture_range,
        )
        if extraction.chunks:
            database.replace_document_chunks(resource["id"], extraction.chunks)
            if not getattr(service.embeddings, "requires_external_upload", False):
                await service.retriever.index_resource(resource["id"])
            resource = database.get_resource(resource["id"])
        reconstruction, learning = service.refresh_scores(session_id)
        resource["session_scores"] = {"reconstruction": reconstruction, "learning_coverage": learning}
        return _public_resource(resource)

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
        return _public_resource(resource)

    @app.get("/resources/{resource_id}")
    def get_resource(resource_id: str) -> dict:
        return _public_resource(database.get_resource(resource_id))

    @app.get("/resources/{resource_id}/raw")
    def get_resource_raw(resource_id: str):
        resource = database.get_resource(resource_id)
        if resource.get("local_path"):
            path = Path(resource["local_path"])
        elif resource.get("storage_key"):
            path = storage.materialize(
                StoredObject(provider=resource["storage_provider"] or "local", key=resource["storage_key"])
            )
        else:
            raise HTTPException(status_code=422, detail="This resource has no stored file")
        if not path.exists():
            raise HTTPException(status_code=404, detail="Stored file is missing")
        return FileResponse(
            path,
            media_type=resource.get("mime_type") or "application/octet-stream",
            filename=resource["name"],
        )

    @app.get("/chunks/{chunk_id}")
    def get_chunk(chunk_id: str) -> dict:
        chunk = database.get_document_chunk(chunk_id)
        public_chunk = _public_chunk(chunk)
        return {
            **public_chunk,
            "resource": {
                "id": chunk["resource_id"],
                "name": chunk.get("resource_name"),
                "type": chunk.get("resource_type"),
                "evidence_level": chunk.get("resource_evidence_level"),
                "mime_type": chunk.get("resource_mime_type"),
            },
            "preview_url": f"/chunks/{chunk_id}/visual" if chunk.get("visual_path") else None,
        }

    @app.get("/chunks/{chunk_id}/visual")
    def get_chunk_visual(chunk_id: str):
        chunk = database.get_document_chunk(chunk_id)
        visual_path = chunk.get("visual_path")
        if not visual_path:
            raise HTTPException(status_code=404, detail="No visual preview is available for this chunk")
        path = Path(visual_path).resolve()
        if not path.is_file() or not path.is_relative_to(settings.data_dir.resolve()):
            raise HTTPException(status_code=404, detail="No visual preview is available for this chunk")
        return FileResponse(path)

    @app.patch("/resources/{resource_id}/quality")
    def update_resource_quality(resource_id: str, payload: ResourceQualityUpdate) -> dict:
        resource = database.update_resource_quality(resource_id, payload.coverage, payload.quality, payload.relevance)
        service.refresh_scores(resource["session_id"])
        return _public_resource(resource)

    @app.post("/resources/{resource_id}/transcribe")
    async def transcribe(resource_id: str, payload: TranscriptionRequest) -> dict:
        resource = database.get_resource(resource_id)
        if resource["type"] not in {"audio", "video"} or not (
            resource["local_path"] or resource.get("storage_key")
        ):
            raise HTTPException(
                status_code=422, detail="Only a locally stored audio or video resource can be transcribed"
            )
        _permission(payload.confirm_external_upload, service.asr)
        if resource["local_path"]:
            media_path = Path(resource["local_path"])
        else:
            media_path = storage.materialize(
                StoredObject(provider=resource["storage_provider"], key=resource["storage_key"])
            )
        if isinstance(service.asr, LocalWhisperProvider):
            session = database.get_session(resource["session_id"])
            segments = await service.asr.transcribe(
                str(media_path), resource["mime_type"], initial_prompt=_local_asr_prompt(session, resource)
            )
        else:
            segments = await service.asr.transcribe(str(media_path), resource["mime_type"])
        database.save_transcript(resource_id, [item.model_dump() for item in segments])
        service.refresh_scores(resource["session_id"])
        return {"resource_id": resource_id, "segments": database.list_transcript_segments(resource_id)}

    @app.post("/sessions/{session_id}/retrieve")
    async def retrieve(session_id: str, payload: RetrievalRequest) -> list[dict]:
        database.get_session(session_id)
        if getattr(service.embeddings, "requires_external_upload", False):
            raise HTTPException(
                status_code=409,
                detail="External embedding retrieval must run inside a consented analysis or assessment operation.",
            )
        return await service.retriever.retrieve(session_id, payload.query, payload.policy, payload.limit)

    @app.post("/sessions/{session_id}/analyze")
    async def analyze(session_id: str, payload: AnalysisRequest) -> dict:
        _permission(payload.confirm_external_upload, service.ai)
        _permission(payload.confirm_external_upload, service.embeddings)
        return _public_session(await service.analyze(session_id))

    async def run_transcription_job(job_id: str, resource_id: str) -> None:
        job = database.get_job(job_id)
        if job["status"] == JobStatus.CANCELLED.value:
            return
        try:
            database.update_job(job_id, status="running", stage="materializing_media", progress=10)
            resource = database.get_resource(resource_id)
            if resource["local_path"]:
                media_path = Path(resource["local_path"])
            else:
                media_path = storage.materialize(
                    StoredObject(provider=resource["storage_provider"], key=resource["storage_key"])
                )
            if isinstance(service.asr, LocalWhisperProvider):
                database.update_job(job_id, stage="loading_local_whisper", progress=20)
            else:
                database.update_job(job_id, stage="transcribing", progress=25)
            if isinstance(service.asr, LocalWhisperProvider):
                session = database.get_session(job["session_id"])
                segments = await service.asr.transcribe(
                    str(media_path), resource["mime_type"], initial_prompt=_local_asr_prompt(session, resource)
                )
            else:
                segments = await service.asr.transcribe(str(media_path), resource["mime_type"])
            database.save_transcript(resource_id, [item.model_dump() for item in segments])
            service.refresh_scores(resource["session_id"])
            database.update_job(
                job_id,
                status="succeeded",
                stage="complete",
                progress=100,
                result={"segment_count": len(segments)},
            )
        except Exception as exc:
            database.update_job(job_id, status="failed", stage="failed", error=str(exc))

    async def run_indexing_job(job_id: str, resource_id: str) -> None:
        try:
            database.update_job(job_id, status="running", stage="embedding_chunks", progress=30)
            count = await service.retriever.index_resource(resource_id)
            database.update_job(
                job_id,
                status="succeeded",
                stage="complete",
                progress=100,
                result={"indexed_chunk_count": count},
            )
        except Exception as exc:
            database.update_job(job_id, status="failed", stage="failed", error=str(exc))

    @app.post("/sessions/{session_id}/jobs", status_code=202)
    def create_job(session_id: str, payload: JobCreate, background: BackgroundTasks) -> dict:
        database.get_session(session_id)
        if payload.kind in {JobKind.ANALYSIS, JobKind.ASSESSMENT}:
            _permission(payload.confirm_external_upload, service.ai)
            _permission(payload.confirm_external_upload, service.embeddings)
        elif payload.kind == JobKind.TRANSCRIPTION:
            _permission(payload.confirm_external_upload, service.asr)
        elif payload.kind == JobKind.INDEXING:
            _permission(payload.confirm_external_upload, service.embeddings)
        if payload.kind in {JobKind.TRANSCRIPTION, JobKind.INDEXING}:
            if not payload.resource_id:
                raise HTTPException(status_code=422, detail="resource_id is required for this job")
            resource = database.get_resource(payload.resource_id)
            if resource["session_id"] != session_id:
                raise HTTPException(status_code=422, detail="resource does not belong to this Session")
        job = database.create_job(
            payload.kind.value,
            session_id=session_id,
            resource_id=payload.resource_id,
            payload={"confirmed_external_upload": payload.confirm_external_upload},
        )
        if payload.kind in {JobKind.ANALYSIS, JobKind.ASSESSMENT}:
            background.add_task(service.run_job, job["id"])
        elif payload.kind == JobKind.TRANSCRIPTION:
            background.add_task(run_transcription_job, job["id"], payload.resource_id)
        else:
            background.add_task(run_indexing_job, job["id"], payload.resource_id)
        return job

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        return database.get_job(job_id)

    @app.get("/sessions/{session_id}/jobs")
    def list_jobs(session_id: str) -> list[dict]:
        database.get_session(session_id)
        return database.list_jobs(session_id)

    @app.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict:
        job = database.get_job(job_id)
        if job["status"] in {JobStatus.SUCCEEDED.value, JobStatus.FAILED.value}:
            raise HTTPException(status_code=409, detail="completed jobs cannot be cancelled")
        return database.update_job(job_id, status="cancelled", stage="cancelled")

    @app.post("/sessions/{session_id}/assessment", status_code=201)
    async def generate_assessment(session_id: str, payload: AnalysisRequest) -> list[dict]:
        _permission(payload.confirm_external_upload, service.ai)
        _permission(payload.confirm_external_upload, service.embeddings)
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
        _permission(payload.confirm_external_upload, service.embeddings)
        return await service.evaluate(question_id, payload.answer)

    @app.post("/knowledge-points/{point_id}/remediation", status_code=201)
    async def remediate(point_id: str, payload: RemediationRequest) -> dict:
        _permission(payload.confirm_external_upload, service.ai)
        _permission(payload.confirm_external_upload, service.embeddings)
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
