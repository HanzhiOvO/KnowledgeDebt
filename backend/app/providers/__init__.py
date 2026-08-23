"""Provider public API without importing every optional adapter eagerly.

Importing ``app.providers.hash_embedding`` first executes this package module.  The
old eager exports therefore pulled in HTTP clients and optional local ASR modules
during unrelated test collection and native startup.  Keep the public names for
compatibility, but load concrete adapters only when callers request them.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

from .base import AIProvider, EmbeddingProvider, TranscriptionProvider

if TYPE_CHECKING:
    from .anthropic import AnthropicProvider
    from .hash_embedding import HashEmbeddingProvider
    from .local_rule import LocalASRProvider, LocalRuleProvider
    from .local_whisper import LocalWhisperProvider
    from .openai_compatible import OpenAICompatibleProvider

__all__ = [
    "AIProvider",
    "AnthropicProvider",
    "EmbeddingProvider",
    "HashEmbeddingProvider",
    "LocalASRProvider",
    "LocalRuleProvider",
    "LocalWhisperProvider",
    "OpenAICompatibleProvider",
    "TranscriptionProvider",
]

_LAZY_EXPORTS = {
    "AnthropicProvider": (".anthropic", "AnthropicProvider"),
    "HashEmbeddingProvider": (".hash_embedding", "HashEmbeddingProvider"),
    "LocalASRProvider": (".local_rule", "LocalASRProvider"),
    "LocalRuleProvider": (".local_rule", "LocalRuleProvider"),
    "LocalWhisperProvider": (".local_whisper", "LocalWhisperProvider"),
    "OpenAICompatibleProvider": (".openai_compatible", "OpenAICompatibleProvider"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _LAZY_EXPORTS[name]
    except KeyError as exc:  # pragma: no cover - Python's normal module fallback
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value
