from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderPreset:
    id: str
    label: str
    base_url: str
    default_model: str
    api_style: str = "openai_chat"
    key_required: bool = True
    description: str = ""


PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "local_rule": ProviderPreset(
        id="local_rule",
        label="本地规则引擎",
        base_url="",
        default_model="local",
        api_style="local",
        key_required=False,
        description="零配置、不联网，适合没有 API Key 时完整演示闭环。",
    ),
    "openai_compatible": ProviderPreset(
        id="openai_compatible",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-5-mini",
        api_style="openai_chat",
        key_required=True,
        description="粘贴 OpenAI 官方 API Key。",
    ),
    "deepseek": ProviderPreset(
        id="deepseek",
        label="DeepSeek",
        base_url="https://api.deepseek.com",
        default_model="deepseek-chat",
        api_style="openai_chat",
        key_required=True,
        description="粘贴 DeepSeek 官方 API Key。",
    ),
    "anthropic": ProviderPreset(
        id="anthropic",
        label="Anthropic / Claude",
        base_url="https://api.anthropic.com",
        default_model="claude-sonnet-4-5",
        api_style="anthropic_messages",
        key_required=True,
        description="粘贴 Anthropic 官方 API Key。Authoripic 拼写按 Anthropic 处理。",
    ),
    "opencode": ProviderPreset(
        id="opencode",
        label="OpenCode Zen",
        base_url="https://opencode.ai/zen/v1",
        default_model="gpt-5.5",
        api_style="openai_chat",
        key_required=True,
        description="粘贴 OpenCode Zen 官方 API Key。",
    ),
}

PROVIDER_ALIASES = {
    "openai": "openai_compatible",
    "authoripic": "anthropic",
    "claude": "anthropic",
    "opencode_zen": "opencode",
    "deepseek_chat": "deepseek",
    "local": "local_rule",
}


def normalize_provider(value: str | None) -> str | None:
    provider = (value or "").strip().lower()
    if not provider or provider == "auto":
        return None
    return PROVIDER_ALIASES.get(provider, provider)


def resolve_preset(provider: str | None) -> ProviderPreset:
    normalized = normalize_provider(provider) or "local_rule"
    return PROVIDER_PRESETS.get(normalized, PROVIDER_PRESETS["openai_compatible"])


def public_presets() -> list[dict[str, Any]]:
    order = ["local_rule", "openai_compatible", "deepseek", "anthropic", "opencode"]
    presets = [PROVIDER_PRESETS[item] for item in order]
    return [
        {
            "id": item.id,
            "label": item.label,
            "base_url": item.base_url,
            "default_model": item.default_model,
            "api_style": item.api_style,
            "key_required": item.key_required,
            "description": item.description,
        }
        for item in presets
    ]
