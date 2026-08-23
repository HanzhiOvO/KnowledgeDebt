from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .providers.presets import normalize_provider, resolve_preset

RUNTIME_PROVIDER_FILE = "runtime-provider.json"


def _runtime_path(data_dir: Path) -> Path:
    return Path(data_dir) / RUNTIME_PROVIDER_FILE


def load_runtime_provider(data_dir: Path) -> dict[str, Any]:
    path = _runtime_path(data_dir)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_runtime_provider(
    data_dir: Path,
    provider: str,
    api_key: str | None,
    base_url: str,
    model: str,
) -> dict[str, Any]:
    path = _runtime_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ai_provider": normalize_provider(provider) or "local_rule",
        "api_key": api_key or None,
        "base_url": base_url,
        "ai_model": model,
    }
    # Best-effort atomic write with owner-only permissions. The API key must
    # never leave the server or be returned to the browser.
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)
    return payload


def mask_api_key(api_key: str | None) -> str | None:
    if not api_key:
        return None
    if len(api_key) <= 7:
        return "****"
    return f"{api_key[:3]}****{api_key[-4:]}"


def runtime_provider_status(settings: Any, configured: bool) -> dict[str, Any]:
    preset = resolve_preset(settings.ai_provider)
    return {
        "provider": settings.ai_provider,
        "label": preset.label,
        "base_url": settings.base_url,
        "model": settings.ai_model,
        "api_style": preset.api_style,
        "configured": configured,
        "masked_api_key": mask_api_key(settings.api_key),
        "local_mode": settings.ai_provider == "local_rule",
    }
