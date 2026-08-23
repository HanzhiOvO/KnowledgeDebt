from __future__ import annotations

from ..config import Settings
from .anthropic import AnthropicProvider
from .base import AIProvider, EmbeddingProvider, TranscriptionProvider
from .hash_embedding import HashEmbeddingProvider
from .local_rule import LocalASRProvider, LocalRuleProvider
from .local_whisper import LocalWhisperProvider
from .openai_compatible import OpenAICompatibleProvider


def build_ai_provider(settings: Settings) -> AIProvider:
    if settings.ai_provider == "local_rule":
        return LocalRuleProvider()
    if settings.ai_provider == "anthropic":
        return AnthropicProvider(
            api_key=settings.api_key,
            base_url=settings.base_url,
            ai_model=settings.ai_model,
            asr_model=settings.asr_model,
            embedding_model=settings.embedding_model,
        )
    return OpenAICompatibleProvider(
        api_key=settings.api_key,
        base_url=settings.base_url,
        ai_model=settings.ai_model,
        asr_model=settings.asr_model,
        embedding_model=settings.embedding_model,
    )


def build_asr_provider(settings: Settings) -> TranscriptionProvider:
    if settings.asr_provider == "local_whisper":
        return LocalWhisperProvider(
            model_name=settings.local_asr_model,
            device=settings.local_asr_device,
            compute_type=settings.local_asr_compute_type,
            language=settings.local_asr_language,
            download_root=settings.data_dir / "models" / "whisper",
        )
    if settings.asr_provider == "local_rule":
        return LocalASRProvider()
    return OpenAICompatibleProvider(
        api_key=settings.api_key,
        base_url=settings.base_url,
        ai_model=settings.ai_model,
        asr_model=settings.asr_model,
        embedding_model=settings.embedding_model,
    )


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "openai_compatible":
        return OpenAICompatibleProvider(
            api_key=settings.api_key,
            base_url=settings.base_url,
            ai_model=settings.ai_model,
            asr_model=settings.asr_model,
            embedding_model=settings.embedding_model,
        )
    return HashEmbeddingProvider()
