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

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(os.getenv("KNOWLEDGEDEBT_DATA_DIR", "./data")).resolve()
        return cls(
            data_dir=data_dir,
            ai_provider=os.getenv("KNOWLEDGEDEBT_AI_PROVIDER", "openai_compatible"),
            asr_provider=os.getenv("KNOWLEDGEDEBT_ASR_PROVIDER", "openai_compatible"),
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            ai_model=os.getenv("KNOWLEDGEDEBT_AI_MODEL", "gpt-5-mini"),
            asr_model=os.getenv("KNOWLEDGEDEBT_ASR_MODEL", "gpt-4o-mini-transcribe"),
        )
