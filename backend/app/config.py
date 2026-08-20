from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

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
    encryption_key: str | None = None
    auto_transcribe: bool = True
    ffmpeg_path: str = "ffmpeg"
    transcription_chunk_seconds: int = 1500
    schedule_sync_interval_minutes: int = 360
    local_asr_binary: str = "whisper-cli"
    local_asr_model: str = ""
    local_asr_model_dir: Path | None = None
    local_asr_language: str = "zh"
    local_asr_threads: int = 0
    local_asr_timeout_seconds: int = 3600
    local_asr_initial_prompt: str = ""
    local_asr_service_base_url: str = ""
    local_asr_service_model: str = ""
    local_asr_service_convert_wav: bool = False
    local_asr_service_path: str = "/audio/transcriptions"

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(os.getenv("KNOWLEDGEDEBT_DATA_DIR", "./data")).resolve()
        model_dir = os.getenv("KNOWLEDGEDEBT_LOCAL_ASR_MODEL_DIR", "").strip()
        return cls(
            data_dir=data_dir,
            ai_provider=os.getenv("KNOWLEDGEDEBT_AI_PROVIDER", "openai_compatible"),
            asr_provider=os.getenv("KNOWLEDGEDEBT_ASR_PROVIDER", "openai_compatible"),
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            ai_model=os.getenv("KNOWLEDGEDEBT_AI_MODEL", "gpt-5-mini"),
            asr_model=os.getenv("KNOWLEDGEDEBT_ASR_MODEL", "gpt-4o-mini-transcribe"),
            embedding_provider=os.getenv("KNOWLEDGEDEBT_EMBEDDING_PROVIDER", "hash"),
            embedding_model=os.getenv("KNOWLEDGEDEBT_EMBEDDING_MODEL", "text-embedding-3-small"),
            storage_provider=os.getenv("KNOWLEDGEDEBT_STORAGE_PROVIDER", "local"),
            s3_bucket=os.getenv("KNOWLEDGEDEBT_S3_BUCKET"),
            s3_endpoint_url=os.getenv("KNOWLEDGEDEBT_S3_ENDPOINT_URL"),
            access_token=os.getenv("KNOWLEDGEDEBT_ACCESS_TOKEN"),
            database_url=os.getenv("KNOWLEDGEDEBT_DATABASE_URL"),
            encryption_key=os.getenv("KNOWLEDGEDEBT_ENCRYPTION_KEY"),
            auto_transcribe=os.getenv("KNOWLEDGEDEBT_AUTO_TRANSCRIBE", "true").lower()
            not in {"0", "false", "no"},
            ffmpeg_path=os.getenv("KNOWLEDGEDEBT_FFMPEG_PATH", "ffmpeg"),
            transcription_chunk_seconds=max(
                60, int(os.getenv("KNOWLEDGEDEBT_TRANSCRIPTION_CHUNK_SECONDS", "1500"))
            ),
            schedule_sync_interval_minutes=max(
                30, int(os.getenv("KNOWLEDGEDEBT_SCHEDULE_SYNC_INTERVAL_MINUTES", "360"))
            ),
            # 本地 ASR：只读取配置，不自动下载模型，也不启用尚未就绪的路由。
            local_asr_binary=os.getenv("KNOWLEDGEDEBT_LOCAL_ASR_BINARY", "whisper-cli").strip(),
            local_asr_model=os.getenv("KNOWLEDGEDEBT_LOCAL_ASR_MODEL", "").strip(),
            local_asr_model_dir=Path(model_dir).expanduser().resolve()
            if model_dir
            else data_dir / "asr-models",
            local_asr_language=os.getenv("KNOWLEDGEDEBT_LOCAL_ASR_LANGUAGE", "zh").strip(),
            local_asr_threads=max(0, int(os.getenv("KNOWLEDGEDEBT_LOCAL_ASR_THREADS", "0"))),
            local_asr_timeout_seconds=max(
                60, int(os.getenv("KNOWLEDGEDEBT_LOCAL_ASR_TIMEOUT_SECONDS", "3600"))
            ),
            local_asr_initial_prompt=os.getenv("KNOWLEDGEDEBT_LOCAL_ASR_INITIAL_PROMPT", "").strip(),
            local_asr_service_base_url=os.getenv("KNOWLEDGEDEBT_LOCAL_ASR_SERVICE_URL", "").strip(),
            local_asr_service_model=os.getenv("KNOWLEDGEDEBT_LOCAL_ASR_SERVICE_MODEL", "").strip(),
            # whisper.cpp server 只接受 WAV；faster-whisper 类服务可直接接收 FLAC。
            local_asr_service_convert_wav=os.getenv(
                "KNOWLEDGEDEBT_LOCAL_ASR_SERVICE_CONVERT_WAV", "false"
            ).lower()
            in {"1", "true", "yes"},
            local_asr_service_path=os.getenv(
                "KNOWLEDGEDEBT_LOCAL_ASR_SERVICE_PATH", "/audio/transcriptions"
            ).strip()
            or "/audio/transcriptions",
        )
