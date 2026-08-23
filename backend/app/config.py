from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .providers.presets import normalize_provider, resolve_preset
from .runtime_settings import load_runtime_provider

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    ai_provider: str
    asr_provider: str
    api_key: str | None
    base_url: str
    ai_model: str
    asr_model: str
    embedding_provider: str = "hash"
    embedding_model: str = "text-embedding-3-small"
    storage_provider: str = "local"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    access_token: str | None = None
    database_url: str | None = None
    local_asr: bool = True
    local_asr_model: str = "small"
    local_asr_device: str = "auto"
    local_asr_compute_type: str = "auto"
    local_asr_language: str | None = None

    @staticmethod
    def _resolve_provider(name: str | None, default: str, *, api_key: str | None) -> str:
        provider = (name or "").strip().lower()
        if provider in {"", "auto"}:
            return default if api_key else "local_rule"
        return provider

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(os.getenv("KNOWLEDGEDEBT_DATA_DIR", "./data")).resolve()
        runtime = load_runtime_provider(data_dir)

        env_ai_provider = (os.getenv("KNOWLEDGEDEBT_AI_PROVIDER") or "").strip().lower()
        runtime_ai_provider = normalize_provider(str(runtime.get("ai_provider") or ""))
        if env_ai_provider not in {"", "auto"}:
            ai_provider_input = normalize_provider(env_ai_provider) or env_ai_provider
        elif runtime_ai_provider:
            ai_provider_input = runtime_ai_provider
        else:
            ai_provider_input = "auto"

        provider_from_runtime = env_ai_provider in {"", "auto"} and bool(runtime_ai_provider)
        if provider_from_runtime:
            api_key = runtime.get("api_key") or os.getenv("OPENAI_API_KEY") or None
        else:
            api_key = os.getenv("OPENAI_API_KEY") or runtime.get("api_key") or None
        ai_provider = cls._resolve_provider(ai_provider_input, "openai_compatible", api_key=api_key)
        preset = resolve_preset(ai_provider)
        if provider_from_runtime:
            base_url = (runtime.get("base_url") or os.getenv("OPENAI_BASE_URL") or preset.base_url).rstrip("/")
            ai_model = runtime.get("ai_model") or os.getenv("KNOWLEDGEDEBT_AI_MODEL") or preset.default_model
        else:
            base_url = (os.getenv("OPENAI_BASE_URL") or runtime.get("base_url") or preset.base_url).rstrip("/")
            ai_model = os.getenv("KNOWLEDGEDEBT_AI_MODEL") or runtime.get("ai_model") or preset.default_model

        local_asr = (os.getenv("KNOWLEDGEDEBT_LOCAL_ASR", "1") or "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        env_asr_provider = (os.getenv("KNOWLEDGEDEBT_ASR_PROVIDER") or "").strip().lower()
        if env_asr_provider not in {"", "auto"}:
            asr_provider = normalize_provider(env_asr_provider) or env_asr_provider
        elif local_asr:
            asr_provider = "local_whisper"
        else:
            asr_provider = cls._resolve_provider("auto", "openai_compatible", api_key=api_key)

        return cls(
            data_dir=data_dir,
            ai_provider=ai_provider,
            asr_provider=asr_provider,
            api_key=api_key,
            base_url=base_url,
            ai_model=ai_model,
            asr_model=os.getenv("KNOWLEDGEDEBT_ASR_MODEL", "gpt-4o-mini-transcribe"),
            embedding_provider=os.getenv("KNOWLEDGEDEBT_EMBEDDING_PROVIDER", "hash"),
            embedding_model=os.getenv("KNOWLEDGEDEBT_EMBEDDING_MODEL", "text-embedding-3-small"),
            storage_provider=os.getenv("KNOWLEDGEDEBT_STORAGE_PROVIDER", "local"),
            s3_bucket=os.getenv("KNOWLEDGEDEBT_S3_BUCKET"),
            s3_endpoint_url=os.getenv("KNOWLEDGEDEBT_S3_ENDPOINT_URL"),
            access_token=os.getenv("KNOWLEDGEDEBT_ACCESS_TOKEN"),
            database_url=os.getenv("KNOWLEDGEDEBT_DATABASE_URL"),
            local_asr=local_asr,
            local_asr_model=os.getenv("KNOWLEDGEDEBT_LOCAL_ASR_MODEL", "small"),
            local_asr_device=os.getenv("KNOWLEDGEDEBT_LOCAL_ASR_DEVICE", "auto"),
            local_asr_compute_type=os.getenv("KNOWLEDGEDEBT_LOCAL_ASR_COMPUTE_TYPE", "auto"),
            local_asr_language=os.getenv("KNOWLEDGEDEBT_LOCAL_ASR_LANGUAGE") or None,
        )
