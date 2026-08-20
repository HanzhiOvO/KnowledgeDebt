from __future__ import annotations

import re
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .automation import AutomationRepository, parse_iso
from .config import Settings
from .database import Database
from .documents import extract_document
from .models import (
    AcademicTermCreate,
    AnalysisRequest,
    AnswerSubmission,
    CourseCreate,
    CourseProfileUpdate,
    EvidenceLevel,
    InboxDecision,
    JobCreate,
    JobKind,
    JobStatus,
    OccurrenceMaterializeRequest,
    ProviderDefaultUpdate,
    ProviderGroup,
    ProviderProfileCreate,
    ProviderProfileUpdate,
    RemediationRequest,
    ResourceQualityUpdate,
    ResourceType,
    ReviewDecision,
    ScheduleConnectionUpdate,
    ScheduleRuleCreate,
    SessionCreate,
    SessionTitleUpdate,
    TranscriptionRequest,
)
from .provider_registry import (
    PROVIDER_CATALOG,
    LoggedAIProvider,
    LoggedEmbeddingProvider,
    ProviderRegistry,
)
from .providers.base import (
    AIProvider,
    EmbeddingProvider,
    ProviderNotConfigured,
    ProviderOutputError,
    ProviderRequestError,
    TranscriptionProvider,
)
from .providers.local_asr import (
    LOCAL_ASR_ADAPTERS,
    LOCAL_SERVICE_ADAPTER,
    assert_local_endpoint,
)
from .providers.openai_compatible import OpenAICompatibleProvider
from .retrieval import RetrievalPolicy
from .schedule import ZJSUConnector, ZJSUFixtureParser
from .scoring import minimum_daily_minutes
from .secrets import SecretStore
from .service import KnowledgeService
from .storage import LocalStorageProvider, S3StorageProvider, StorageProvider
from .transcription import MediaPreparer, TranscriptionOrchestrator


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
    embedding_provider: EmbeddingProvider | None = None,
    storage_provider: StorageProvider | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    database = db or Database(settings.database_url or settings.data_dir / "knowledgedebt.sqlite3")
    default_provider = OpenAICompatibleProvider(
        settings.api_key,
        settings.base_url,
        settings.ai_model,
        settings.asr_model,
        settings.embedding_model,
    )
    selected_embedding_provider = embedding_provider
    if selected_embedding_provider is None and settings.embedding_provider == "openai_compatible":
        selected_embedding_provider = default_provider
    service = KnowledgeService(
        database,
        ai_provider or default_provider,
        asr_provider or default_provider,
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

    automation = AutomationRepository(database)
    secret_store = SecretStore(settings.encryption_key)
    provider_registry = ProviderRegistry(automation, secret_store, settings)
    provider_registry.ensure_environment_profiles()

    def injected_profile(name: str, provider: object, model: str) -> dict:
        return {
            "id": None,
            "name": name,
            "vendor": name,
            "default_model": model,
            "external": bool(getattr(provider, "requires_external_upload", False)),
            "capabilities": [],
        }

    def active_ai() -> tuple[dict, AIProvider]:
        if ai_provider is not None:
            profile = injected_profile(settings.ai_provider, ai_provider, settings.ai_model)
            service.ai = cast(AIProvider, LoggedAIProvider(ai_provider, profile, automation))
            return profile, service.ai
        profile, resolved = provider_registry.default("ai")
        service.ai = cast(AIProvider, LoggedAIProvider(resolved, profile, automation))
        return profile, service.ai

    def active_asr() -> tuple[dict, TranscriptionProvider]:
        if asr_provider is not None:
            profile = injected_profile(settings.asr_provider, asr_provider, settings.asr_model)
            profile["capabilities"] = ["long_audio", "segment_timestamps"]
            return profile, asr_provider
        profile, resolved = provider_registry.default("asr")
        service.asr = cast(TranscriptionProvider, resolved)
        return profile, service.asr

    def active_embedding() -> tuple[dict, EmbeddingProvider]:
        if embedding_provider is not None:
            profile = injected_profile(
                settings.embedding_provider, embedding_provider, settings.embedding_model
            )
            service.embeddings = cast(
                EmbeddingProvider,
                LoggedEmbeddingProvider(embedding_provider, profile, automation),
            )
            service.retriever.embeddings = service.embeddings
            return profile, service.embeddings
        profile, resolved = provider_registry.default("embedding")
        service.embeddings = cast(
            EmbeddingProvider,
            LoggedEmbeddingProvider(resolved, profile, automation),
        )
        service.retriever.embeddings = service.embeddings
        return profile, service.embeddings

    transcriber = TranscriptionOrchestrator(
        database,
        automation,
        storage,
        active_asr,
        MediaPreparer(
            settings.ffmpeg_path,
            settings.data_dir / "transcription-chunks",
            settings.transcription_chunk_seconds,
        ),
        service.refresh_scores,
    )
    zjsu_connector = ZJSUConnector()
    zjsu_parser = ZJSUFixtureParser()

    def enriched_session(session_id: str) -> dict:
        session = database.get_session(session_id)
        session["automation"] = automation.session_automation(session_id)
        for resource in session["resources"]:
            state = automation.ensure_resource_automation(resource["id"])
            resource["automation"] = state
            resource["active_transcription_job"] = automation.active_transcription_job(resource["id"])
        return session

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await transcriber.adopt_unfinished()
        yield
        await transcriber.shutdown()

    app = FastAPI(
        title="KnowledgeDebt API",
        version="0.2.0",
        description="Local-first course reconstruction and mastery assessment API",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = database
    app.state.service = service
    app.state.storage = storage
    app.state.automation = automation
    app.state.provider_registry = provider_registry
    app.state.transcriber = transcriber
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

    @app.exception_handler(PermissionError)
    async def permission_handler(_, exc: PermissionError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": "0.2.0"}

    @app.get("/settings/provider")
    def provider_settings() -> dict:
        defaults = automation.get_provider_defaults()
        return {
            "ai_provider": settings.ai_provider,
            "asr_provider": settings.asr_provider,
            "ai_model": settings.ai_model,
            "asr_model": settings.asr_model,
            "embedding_provider": settings.embedding_provider,
            "embedding_model": settings.embedding_model,
            "storage_provider": storage.name,
            "configured": bool(settings.api_key),
            "external_upload_requires_confirmation": True,
            "profiles": automation.list_provider_profiles(),
            "defaults": defaults,
            "secret_encryption_configured": secret_store.configured,
            "local_asr": provider_registry.local_asr_status(),
        }

    @app.get("/settings/providers/catalog")
    def provider_catalog() -> list[dict]:
        return PROVIDER_CATALOG

    @app.get("/settings/providers")
    def list_provider_profiles() -> list[dict]:
        return automation.list_provider_profiles()

    def normalize_local_asr(values: dict, adapter: str) -> None:
        """本地 ASR Profile 一律标记为本地，并拒绝伪装成本地的公网地址。"""

        if adapter not in LOCAL_ASR_ADAPTERS:
            return
        values["external"] = False
        if adapter != LOCAL_SERVICE_ADAPTER or "base_url" not in values:
            return
        try:
            values["base_url"] = assert_local_endpoint(values.get("base_url") or "")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/settings/providers", status_code=201)
    def create_provider_profile(payload: ProviderProfileCreate) -> dict:
        values = payload.model_dump(mode="json")
        catalog = next(
            (
                item
                for item in PROVIDER_CATALOG
                if item["vendor"] == payload.vendor and item["adapter"] == payload.adapter
            ),
            None,
        )
        implementation_status = catalog["implementation_status"] if catalog else "user_verified"
        if implementation_status == "interface_slot" and payload.enabled:
            raise HTTPException(
                status_code=422,
                detail="该适配器目前只有接口槽位，未完成真实验证，不能启用。",
            )
        credential = values.pop("credential", None)
        if credential:
            values["credential_ciphertext"] = secret_store.encrypt(credential)
            values["credential_reference"] = None
        reference = values.get("credential_reference")
        if reference and not reference.startswith("env:"):
            raise HTTPException(status_code=422, detail="credential_reference 仅支持 env:VARIABLE")
        values["implementation_status"] = implementation_status
        normalize_local_asr(values, payload.adapter)
        return automation.create_provider_profile(values)

    @app.patch("/settings/providers/{profile_id}")
    def update_provider_profile(profile_id: str, payload: ProviderProfileUpdate) -> dict:
        values = payload.model_dump(exclude_unset=True, mode="json")
        normalize_local_asr(values, automation.get_provider_profile(profile_id)["adapter"])
        credential = values.pop("credential", None)
        if credential:
            values["credential_ciphertext"] = secret_store.encrypt(credential)
            values["credential_reference"] = None
        reference = values.get("credential_reference")
        if reference and not reference.startswith("env:"):
            raise HTTPException(status_code=422, detail="credential_reference 仅支持 env:VARIABLE")
        return automation.update_provider_profile(profile_id, values)

    @app.delete("/settings/providers/{profile_id}")
    def delete_provider_profile(profile_id: str) -> dict:
        automation.delete_provider_profile(profile_id)
        return {"deleted": True, "id": profile_id}

    @app.get("/settings/providers/defaults")
    def provider_defaults() -> dict:
        return automation.get_provider_defaults()

    @app.put("/settings/providers/defaults/{provider_group}")
    def set_provider_default(provider_group: ProviderGroup, payload: ProviderDefaultUpdate) -> dict:
        profile = automation.get_provider_profile(payload.profile_id)
        required = {
            "ai": {"structured_generation", "chat_analysis"},
            "asr": {"audio_transcription", "async_audio_transcription"},
            "embedding": {"embeddings"},
        }[provider_group.value]
        if not required.intersection(profile["capabilities"]):
            raise HTTPException(
                status_code=422,
                detail=f"该 Profile 不声明 {provider_group.value} 所需能力，不能设为默认。",
            )
        return automation.set_provider_default(provider_group.value, payload.profile_id)

    @app.post("/settings/providers/{profile_id}/test")
    async def test_provider_profile(profile_id: str) -> dict:
        return await provider_registry.test_connection(profile_id)

    @app.get("/settings/provider-usage")
    def provider_usage(month: str | None = None) -> dict:
        return automation.provider_usage(month)

    @app.get("/schedule/terms")
    def list_terms() -> list[dict]:
        return automation.list_terms()

    @app.post("/schedule/terms", status_code=201)
    def create_term(payload: AcademicTermCreate) -> dict:
        return automation.create_term(payload.model_dump())

    @app.get("/schedule/connection")
    def schedule_connection() -> dict:
        try:
            connection = automation.get_schedule_connection(zjsu_connector.connector_id)
        except KeyError:
            connection = automation.upsert_schedule_connection(
                ScheduleConnectionUpdate(
                    sync_interval_minutes=settings.schedule_sync_interval_minutes
                ).model_dump()
            )
        return {**connection, "capability": zjsu_connector.capability.__dict__, "base_url": zjsu_connector.base_url}

    @app.put("/schedule/connection")
    def update_schedule_connection(payload: ScheduleConnectionUpdate) -> dict:
        if payload.connector != zjsu_connector.connector_id:
            raise HTTPException(status_code=422, detail="当前版本只提供浙江工商大学本科教务连接器")
        return automation.upsert_schedule_connection(payload.model_dump())

    @app.post("/schedule/connection/login")
    def begin_schedule_login(mode: str = "account") -> dict:
        connection = schedule_connection()
        result = zjsu_connector.begin_login(mode)
        automation.update_schedule_connection_state(
            zjsu_connector.connector_id,
            state=result["state"],
            error=result["message"],
        )
        return {**connection, **result}

    @app.post("/schedule/import-fixture")
    async def import_schedule_fixture(file: UploadFile = File(...)) -> dict:
        schedule_connection()
        raw = await file.read(2_000_001)
        if len(raw) > 2_000_000:
            raise HTTPException(status_code=413, detail="fixture 不能超过 2 MB")
        automation.update_schedule_connection_state(zjsu_connector.connector_id, state="syncing")
        try:
            parsed = zjsu_parser.parse(raw)
            term_data = parsed["term"]
            term = next(
                (
                    item
                    for item in automation.list_terms()
                    if item["name"] == term_data["name"]
                    and item["starts_on"] == term_data["starts_on"]
                    and item["ends_on"] == term_data["ends_on"]
                ),
                None,
            )
            if not term:
                term = automation.create_term(
                    {
                        "name": term_data["name"],
                        "starts_on": term_data["starts_on"],
                        "ends_on": term_data["ends_on"],
                        "timezone": term_data.get("timezone", "Asia/Shanghai"),
                        "current": bool(term_data.get("current", True)),
                    }
                )
            rules_by_external: dict[str, dict] = {}
            for rule_data in parsed["rules"]:
                rule = automation.upsert_schedule_rule({**rule_data, "term_id": term["id"]})
                rules_by_external[rule["external_id"]] = rule
            occurrences: list[dict] = []
            materialized = 0
            for occurrence_data in parsed["occurrences"]:
                rule = rules_by_external.get(occurrence_data.pop("rule_external_id"))
                if not rule:
                    raise ValueError("adjustment references an unknown rule_external_id")
                occurrence = automation.upsert_occurrence({**occurrence_data, "rule_id": rule["id"]})
                occurrences.append(occurrence)
                if (
                    occurrence["status"] != "cancelled"
                    and parse_iso(occurrence["ends_at"]) <= datetime.now(UTC)
                ):
                    automation.materialize_occurrence(occurrence["id"], "occurred")
                    materialized += 1
            connection = automation.update_schedule_connection_state(
                zjsu_connector.connector_id,
                state="connected",
                synced=True,
                error=None,
            )
        except Exception as exc:
            automation.update_schedule_connection_state(
                zjsu_connector.connector_id,
                state="error",
                error=str(exc),
            )
            raise
        return {
            "connection": connection,
            "term": term,
            "rule_count": len(rules_by_external),
            "occurrence_count": len(occurrences),
            "materialized_session_count": materialized,
        }

    @app.get("/schedule/rules")
    def list_schedule_rules(term_id: str | None = None) -> list[dict]:
        return automation.list_schedule_rules(term_id)

    @app.post("/schedule/rules", status_code=201)
    def create_schedule_rule(payload: ScheduleRuleCreate) -> dict:
        return automation.upsert_schedule_rule(payload.model_dump())

    @app.get("/schedule/occurrences")
    def list_occurrences(starts_on: str | None = None, ends_on: str | None = None) -> list[dict]:
        return automation.list_occurrences(starts_on, ends_on)

    @app.post("/schedule/occurrences/{occurrence_id}/materialize", status_code=201)
    def materialize_occurrence(occurrence_id: str, payload: OccurrenceMaterializeRequest) -> dict:
        return automation.materialize_occurrence(occurrence_id, payload.reason)

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
        return enriched_session(session_id)

    @app.patch("/sessions/{session_id}/title")
    def update_session_title(session_id: str, payload: SessionTitleUpdate) -> dict:
        return automation.update_session_title(
            session_id,
            payload.title,
            source="user",
            confidence=1,
            locked=payload.locked,
        )

    @app.get("/sessions/{session_id}/consent-manifest")
    def consent_manifest(session_id: str, operation: str, resource_id: str | None = None) -> dict:
        session = database.get_session(session_id)
        if operation not in {"analysis", "assessment", "transcription", "indexing"}:
            raise HTTPException(status_code=422, detail="unsupported operation")
        ai_profile, ai = active_ai()
        embedding_profile, embeddings = active_embedding()
        provider_entries = [(ai_profile, ai), (embedding_profile, embeddings)]
        resources = session["resources"]
        sends = ["Session title and notes", "retrieved transcript segments", "retrieved document chunks"]
        does_not_send = ["local filesystem paths", "unselected document chunks", "original audio/video binaries"]
        if operation == "transcription":
            asr_profile, asr = active_asr()
            provider_entries = [(asr_profile, asr)]
            resources = [item for item in resources if item["id"] == resource_id]
            if not resources:
                raise HTTPException(status_code=422, detail="select a Session audio or video resource")
            sends = ["the selected original audio/video binary", "its MIME type and filename"]
            does_not_send = ["other Session resources", "course history", "local filesystem paths"]
        elif operation == "indexing":
            provider_entries = [(embedding_profile, embeddings)]
            resources = [item for item in resources if not resource_id or item["id"] == resource_id]
            sends = ["text chunks from the listed resources"]
            does_not_send = ["original files", "audio/video binaries", "local filesystem paths"]
        external_providers = [
            profile["name"] for profile, provider in provider_entries
            if profile.get("external") or getattr(provider, "requires_external_upload", False)
        ]
        return {
            "operation": operation,
            "provider": ", ".join(external_providers) if external_providers else "local providers",
            "providers": [
                {
                    "id": profile.get("id"),
                    "name": profile["name"],
                    "vendor": profile.get("vendor"),
                    "model": profile.get("default_model"),
                    "external": bool(profile.get("external")),
                }
                for profile, _ in provider_entries
            ],
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
        background: BackgroundTasks,
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
        auto_transcribe: bool = Form(True),
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
        resource_automation = automation.ensure_resource_automation(
            resource["id"], state="saved", auto_transcribe=auto_transcribe
        )
        transcription_job = None
        if (
            resource_type in {ResourceType.AUDIO, ResourceType.VIDEO}
            and settings.auto_transcribe
            and auto_transcribe
        ):
            profile, _ = active_asr()
            if profile.get("external"):
                resource_automation = automation.update_resource_transcription(
                    resource["id"], "awaiting_consent"
                )
            else:
                transcription_job, created = transcriber.create_job(
                    resource["id"], confirmed_external_upload=False, profile=profile
                )
                resource_automation = automation.get_resource_automation(resource["id"])
                if created:
                    background.add_task(transcriber.run, transcription_job["id"])
        resource["session_scores"] = {"reconstruction": reconstruction, "learning_coverage": learning}
        resource["automation"] = resource_automation
        resource["transcription_job"] = transcription_job
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
        if resource["type"] not in {"audio", "video"} or not (
            resource["local_path"] or resource.get("storage_key")
        ):
            raise HTTPException(
                status_code=422, detail="Only a locally stored audio or video resource can be transcribed"
            )
        state = automation.ensure_resource_automation(resource_id)
        if state["transcription_state"] == "transcribed":
            return {
                "resource_id": resource_id,
                "segments": database.list_transcript_segments(resource_id),
                "job": database.get_job(state["last_job_id"]) if state.get("last_job_id") else None,
            }
        profile, _ = active_asr()
        job, created = transcriber.create_job(
            resource_id,
            confirmed_external_upload=payload.confirm_external_upload,
            profile=profile,
        )
        if created:
            job = await transcriber.run(job["id"])
        return {
            "resource_id": resource_id,
            "segments": database.list_transcript_segments(resource_id),
            "job": job,
            "automation": automation.get_resource_automation(resource_id),
        }

    @app.post("/resources/{resource_id}/transcription-jobs", status_code=202)
    def create_transcription_job(
        resource_id: str,
        payload: TranscriptionRequest,
        background: BackgroundTasks,
    ) -> dict:
        profile, _ = active_asr()
        job, created = transcriber.create_job(
            resource_id,
            confirmed_external_upload=payload.confirm_external_upload,
            profile=profile,
        )
        if created:
            background.add_task(transcriber.run, job["id"])
        return {**job, "deduplicated": not created}

    @app.get("/resources/{resource_id}/transcription")
    def resource_transcription(resource_id: str) -> dict:
        return {
            "resource_id": resource_id,
            "automation": automation.get_resource_automation(resource_id),
            "chunks": automation.list_transcription_chunks(resource_id),
            "segments": database.list_transcript_segments(resource_id),
            "active_job": automation.active_transcription_job(resource_id),
        }

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
        _, ai = active_ai()
        _, embeddings = active_embedding()
        _permission(payload.confirm_external_upload, ai)
        _permission(payload.confirm_external_upload, embeddings)
        return await service.analyze(session_id)

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
            _, ai = active_ai()
            _, embeddings = active_embedding()
            _permission(payload.confirm_external_upload, ai)
            _permission(payload.confirm_external_upload, embeddings)
        elif payload.kind == JobKind.TRANSCRIPTION:
            if not payload.resource_id:
                raise HTTPException(status_code=422, detail="resource_id is required for this job")
            resource = database.get_resource(payload.resource_id)
            if resource["session_id"] != session_id:
                raise HTTPException(status_code=422, detail="resource does not belong to this Session")
            profile, _ = active_asr()
            job, created = transcriber.create_job(
                payload.resource_id,
                confirmed_external_upload=payload.confirm_external_upload,
                profile=profile,
            )
            if created:
                background.add_task(transcriber.run, job["id"])
            return {**job, "deduplicated": not created}
        elif payload.kind == JobKind.INDEXING:
            _, embeddings = active_embedding()
            _permission(payload.confirm_external_upload, embeddings)
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
        cancelled = database.cancel_job(job_id)
        if (
            cancelled["status"] == JobStatus.CANCELLED.value
            and job["status"] in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}
            and job["kind"] == "transcription"
            and job.get("resource_id")
        ):
            automation.update_resource_transcription(job["resource_id"], "cancelled", job_id=job_id)
        return cancelled

    @app.post("/sessions/{session_id}/assessment", status_code=201)
    async def generate_assessment(session_id: str, payload: AnalysisRequest) -> list[dict]:
        _, ai = active_ai()
        _, embeddings = active_embedding()
        _permission(payload.confirm_external_upload, ai)
        _permission(payload.confirm_external_upload, embeddings)
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
        _, ai = active_ai()
        _, embeddings = active_embedding()
        _permission(payload.confirm_external_upload, ai)
        _permission(payload.confirm_external_upload, embeddings)
        return await service.evaluate(question_id, payload.answer)

    @app.post("/knowledge-points/{point_id}/remediation", status_code=201)
    async def remediate(point_id: str, payload: RemediationRequest) -> dict:
        _, ai = active_ai()
        _, embeddings = active_embedding()
        _permission(payload.confirm_external_upload, ai)
        _permission(payload.confirm_external_upload, embeddings)
        return await service.remediate(point_id, payload.reason)

    @app.post("/learning-steps/{step_id}/complete")
    def complete_learning_step(step_id: str) -> dict:
        database.complete_learning_step(step_id)
        return {"id": step_id, "completed": True}

    def match_inbox_item(item_id: str, background: BackgroundTasks) -> dict:
        item = automation.get_inbox_item(item_id)
        captured = parse_iso(item["captured_at"])
        filename = item["name"].lower()
        extracted = (item.get("extracted_text") or "").lower()
        courses = {course["id"]: course for course in database.list_courses()}
        candidates: list[tuple[float, str | None, str | None, list[str]]] = []
        for session in database.list_sessions():
            course = courses[session["course_id"]]
            score, reasons = 0.0, []
            if session.get("starts_at") and session.get("ends_at"):
                starts, ends = parse_iso(session["starts_at"]), parse_iso(session["ends_at"])
                if starts - (ends - starts) <= captured <= ends + (ends - starts):
                    score += 0.65
                    reasons.append("采集时间落在该课堂时段附近")
            if course["name"].lower() in filename:
                score += 0.25
                reasons.append("文件名包含课程名称")
            if course["name"].lower() in extracted:
                score += 0.15
                reasons.append("提取文本包含课程名称")
            if course.get("teacher") and str(course["teacher"]).lower() in filename:
                score += 0.1
                reasons.append("文件名包含教师信息")
            if score:
                candidates.append((min(1, score), session["id"], None, reasons))
        for occurrence in automation.list_occurrences(
            (captured.date()).isoformat(), (captured.date()).isoformat()
        ):
            if occurrence["status"] == "cancelled" or occurrence.get("session_id"):
                continue
            starts, ends = parse_iso(occurrence["starts_at"]), parse_iso(occurrence["ends_at"])
            rule = occurrence["rule"]
            score, reasons = 0.0, []
            if starts - (ends - starts) <= captured <= ends + (ends - starts):
                score += 0.65
                reasons.append("采集时间与课表课堂实例吻合")
            names = [rule["course_name"], *(rule.get("aliases") or [])]
            if any(name.lower() in filename for name in names if name):
                score += 0.25
                reasons.append("文件名命中课程名称或别名")
            if rule.get("room") and str(rule["room"]).lower() in filename:
                score += 0.1
                reasons.append("文件名命中教室")
            if score:
                candidates.append((min(1, score), None, occurrence["id"], reasons))
        candidates.sort(key=lambda candidate: -candidate[0])
        confidence, session_id, occurrence_id, reasons = candidates[0] if candidates else (0.0, None, None, [])
        if confidence >= 0.8:
            if occurrence_id:
                session_id = automation.materialize_occurrence(occurrence_id, "evidence")["id"]
            if not session_id:
                raise ValueError("high-confidence match did not resolve a Session")
            resource = automation.adopt_inbox_item(item_id, session_id)
            state = automation.ensure_resource_automation(resource["id"])
            if resource["type"] in {"audio", "video"} and settings.auto_transcribe:
                profile, _ = active_asr()
                if profile.get("external"):
                    state = automation.update_resource_transcription(resource["id"], "awaiting_consent")
                else:
                    job, created = transcriber.create_job(
                        resource["id"], confirmed_external_upload=False, profile=profile
                    )
                    if created:
                        background.add_task(transcriber.run, job["id"])
                    state = automation.get_resource_automation(resource["id"])
            return {
                "item": automation.get_inbox_item(item_id),
                "matched": True,
                "resource": resource,
                "transcription": state,
                "confidence": confidence,
                "reasons": reasons,
            }
        status = "review" if confidence >= 0.55 else "pending"
        updated = automation.update_inbox_match(
            item_id,
            status=status,
            confidence=confidence,
            reasons=reasons or ["没有足够可靠的时间、课程名、教师或教室证据"],
            suggested_session_id=session_id,
        )
        automation.create_review_item(
            "archive_match",
            "inbox_item",
            item_id,
            "确认资料归档位置",
            proposed_value=session_id,
            confidence=confidence,
            reasons=updated["match_reasons"],
            navigation_path="/review",
        )
        return {"item": updated, "matched": False, "confidence": confidence, "reasons": updated["match_reasons"]}

    @app.post("/inbox/upload", status_code=201)
    async def upload_inbox_item(
        background: BackgroundTasks,
        file: UploadFile = File(...),
        resource_type: ResourceType = Form(...),
        captured_at: str | None = Form(None),
        original_file_time: str | None = Form(None),
        source: str = Form("global_upload"),
    ) -> dict:
        upload_id = uuid.uuid4().hex
        key = f"inbox/{upload_id}_{_safe_name(file.filename or 'resource')}"
        stored = storage.save(key, file.file, file.content_type)
        target = storage.materialize(stored)
        extraction = extract_document(target, file.content_type, settings.data_dir / "derived" / upload_id)
        item = automation.create_inbox_item(
            {
                "name": file.filename or target.name,
                "mime_type": file.content_type,
                "type": resource_type.value,
                "storage_provider": stored.provider,
                "storage_key": stored.key,
                "local_path": str(target) if stored.local_path else None,
                "captured_at": captured_at or datetime.now(UTC).isoformat(),
                "original_file_time": original_file_time,
                "extracted_text": extraction.text,
                "source": source,
            }
        )
        return match_inbox_item(item["id"], background)

    @app.get("/inbox")
    def list_inbox_items() -> list[dict]:
        return automation.list_inbox_items()

    @app.post("/inbox/{item_id}/match")
    def rematch_inbox_item(item_id: str, background: BackgroundTasks) -> dict:
        return match_inbox_item(item_id, background)

    @app.post("/inbox/{item_id}/accept")
    def accept_inbox_item(item_id: str, payload: InboxDecision) -> dict:
        item = automation.get_inbox_item(item_id)
        session_id = payload.session_id or item.get("suggested_session_id")
        if not session_id:
            raise HTTPException(status_code=422, detail="请选择要归档到的 Session")
        resource = automation.adopt_inbox_item(item_id, session_id)
        automation.ensure_resource_automation(resource["id"])
        return {"item": automation.get_inbox_item(item_id), "resource": resource}

    @app.post("/inbox/{item_id}/reject")
    def reject_inbox_item(item_id: str, payload: InboxDecision) -> dict:
        return automation.update_inbox_match(
            item_id,
            status="rejected",
            confidence=0,
            reasons=[payload.reason or "用户拒绝该匹配"],
            suggested_session_id=None,
        )

    @app.post("/inbox/{item_id}/unarchive")
    def unarchive_inbox_item(item_id: str) -> dict:
        return automation.unarchive_inbox_item(item_id)

    @app.get("/reviews")
    def list_reviews(status: str = "pending") -> list[dict]:
        return automation.list_review_items(status)

    @app.post("/reviews/{review_id}/decision")
    def decide_review(review_id: str, payload: ReviewDecision) -> dict:
        review = automation.get_review_item(review_id)
        if review["status"] != "pending":
            return review
        if payload.action in {"accept", "edit_accept"}:
            value = payload.edited_value or review.get("proposed_value")
            if review["kind"] == "archive_match":
                if not value:
                    raise HTTPException(status_code=422, detail="请先选择 Session")
                automation.adopt_inbox_item(review["subject_id"], value)
            elif review["kind"] == "session_topic":
                if not value:
                    raise HTTPException(status_code=422, detail="主题标题不能为空")
                automation.update_session_title(
                    review["subject_id"],
                    value,
                    source="user_review" if payload.action == "edit_accept" else "transcript_rule",
                    confidence=1 if payload.action == "edit_accept" else review["confidence"],
                    locked=payload.action == "edit_accept",
                )
        return automation.decide_review(review_id, payload.action, payload.reason)

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
        today = datetime.now().date().isoformat()
        today_occurrences = automation.list_occurrences(today, today)
        jobs = database.list_jobs()
        pending_automation = []
        for session in sessions:
            for resource in database.list_resources(session["id"]):
                if resource["type"] in {"audio", "video"}:
                    state = automation.ensure_resource_automation(resource["id"])
                    if state["transcription_state"] not in {"transcribed", "cancelled"}:
                        pending_automation.append(
                            {
                                "kind": "transcription",
                                "session_id": session["id"],
                                "resource_id": resource["id"],
                                "state": state["transcription_state"],
                                "name": resource["name"],
                            }
                        )
        return {
            "sessions": session_cards,
            "open_debt_count": len(open_debts),
            "urgent_debt_count": sum(item["priority"] >= 4 for item in open_debts),
            "pending_session_count": len(pending_sessions),
            "minimum_minutes": minimum_daily_minutes(open_debts) + len(unanalyzed_sessions) * 5,
            "today_occurrences": today_occurrences,
            "pending_automation": pending_automation,
            "pending_review_count": len(automation.list_review_items()),
            "jobs": jobs[:12],
        }

    return app


app = create_app()
