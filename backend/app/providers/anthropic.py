from __future__ import annotations

import json
import re
from typing import TypeVar

import httpx
from pydantic import BaseModel

from ..models import TranscriptSegment
from .base import ProviderNotConfigured, ProviderRequestError
from .openai_compatible import SYSTEM_RULES, OpenAICompatibleProvider

T = TypeVar("T", bound=BaseModel)


class AnthropicProvider(OpenAICompatibleProvider):
    """Anthropic Messages API adapter.

    The session-analysis prompt builders are shared with the OpenAI-compatible
    provider; only the HTTP transport and response shape differ here.
    """

    name = "anthropic"
    api_style = "anthropic_messages"

    async def _structured(self, model_type: type[T], prompt: str) -> T:
        if not self.api_key:
            raise ProviderNotConfigured("ANTHROPIC_API_KEY is not configured")
        schema = model_type.model_json_schema()
        body = {
            "model": self.ai_model,
            "max_tokens": 8192,
            "system": SYSTEM_RULES,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"{prompt}\n\nReturn only valid JSON matching this JSON schema exactly. "
                        "Do not use markdown fences.\n"
                        f"JSON schema: {json.dumps(schema, ensure_ascii=False)}"
                    ),
                }
            ],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(f"{self.base_url}/v1/messages", headers=headers, json=body)
            if not response.is_success:
                raise ProviderRequestError(f"Anthropic provider returned HTTP {response.status_code}")
            payload = response.json()
        content = payload.get("content", [])
        if isinstance(content, str):
            content_text = content
        else:
            content_text = "".join(
                str(block.get("text", "")) for block in content if isinstance(block, dict)
            )
        content_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", content_text.strip(), flags=re.M)
        return model_type.model_validate_json(content_text)

    async def transcribe(self, path: str, mime_type: str | None) -> list[TranscriptSegment]:
        del path, mime_type
        raise ProviderNotConfigured(
            "Anthropic 不提供语音转写接口。本项目默认使用本地 Whisper；"
            "如要使用云端 ASR，请将 KNOWLEDGEDEBT_ASR_PROVIDER 设置为 openai_compatible。"
        )

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        del texts
        raise ProviderNotConfigured("Anthropic provider does not expose an embeddings endpoint")
