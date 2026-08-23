from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.providers.local_whisper import LocalWhisperProvider


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        data_dir=tmp_path,
        ai_provider="local_rule",
        asr_provider="local_whisper",
        api_key=None,
        base_url="",
        ai_model="local",
        asr_model="test",
    )
    return TestClient(create_app(settings))


def test_one_key_deepseek_configuration_persists_without_leaking_key(tmp_path: Path, monkeypatch):
    client = make_client(tmp_path)

    options = client.get("/settings/providers").json()
    preset_ids = {preset["id"] for preset in options["presets"]}
    assert {"local_rule", "openai_compatible", "deepseek", "anthropic", "opencode"} <= preset_ids

    response = client.post(
        "/settings/model-provider",
        json={
            "provider": "deepseek",
            "api_key": "sk-abcdef1234567890",
            "model": "deepseek-chat",
        },
    )
    assert response.status_code == 200, response.text
    current = response.json()["current"]
    assert current["ai_provider"] == "deepseek"
    assert current["configured"] is True
    assert current["masked_api_key"] == "sk-****7890"
    assert "sk-abcdef1234567890" not in response.text

    persisted = json.loads((tmp_path / "runtime-provider.json").read_text(encoding="utf-8"))
    assert persisted["ai_provider"] == "deepseek"
    assert persisted["api_key"] == "sk-abcdef1234567890"
    assert persisted["base_url"] == "https://api.deepseek.com"

    monkeypatch.setenv("KNOWLEDGEDEBT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KNOWLEDGEDEBT_AI_PROVIDER", "auto")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("KNOWLEDGEDEBT_AI_MODEL", "gpt-5-mini")
    reloaded = Settings.from_env()
    assert reloaded.ai_provider == "deepseek"
    assert reloaded.api_key == "sk-abcdef1234567890"
    assert reloaded.base_url == "https://api.deepseek.com"
    assert reloaded.ai_model == "deepseek-chat"

    local = client.post("/settings/model-provider/local")
    assert local.status_code == 200
    assert local.json()["current"]["local_mode"] is True
    assert local.json()["current"]["masked_api_key"] is None


def test_anthropic_and_opencode_presets_are_accepted(tmp_path: Path):
    client = make_client(tmp_path)

    anthropic = client.post(
        "/settings/model-provider",
        json={"provider": "authoripic", "api_key": "sk-ant-test", "model": "claude-sonnet-4-5"},
    )
    assert anthropic.status_code == 200
    assert anthropic.json()["current"]["ai_provider"] == "anthropic"
    assert anthropic.json()["current"]["ai_label"] == "Anthropic / Claude"

    opencode = client.post(
        "/settings/model-provider",
        json={"provider": "opencode", "api_key": "oc-test-key", "model": "gpt-5.5"},
    )
    assert opencode.status_code == 200
    assert opencode.json()["current"]["ai_provider"] == "opencode"
    assert opencode.json()["current"]["base_url"] == "https://opencode.ai/zen/v1"


def test_local_whisper_provider_uses_faster_whisper(tmp_path: Path, monkeypatch):
    class FakeSegment:
        start = 1.0
        end = 3.5
        text = "  你好世界 hello world  "

    class FakeInfo:
        language = "zh"
        duration = 4.0

    class FakeWhisperModel:
        def __init__(self, *_args, **_kwargs):
            pass

        def transcribe(self, path: str, **kwargs):
            del path
            assert kwargs["language"] is None
            assert kwargs["vad_filter"] is True
            return iter([FakeSegment()]), FakeInfo()

    fake_module = types.ModuleType("faster_whisper")
    fake_module.__spec__ = object()
    fake_module.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)

    provider = LocalWhisperProvider(
        model_name="fake-model",
        device="cpu",
        compute_type="int8",
        download_root=tmp_path / "models",
    )
    segments = asyncio.run(
        provider.transcribe("/tmp/lecture.wav", "audio/wav", initial_prompt="课程主题：中值定理")
    )
    assert len(segments) == 1
    assert segments[0].text == "你好世界 hello world"
    assert segments[0].start_time == 1.0
    assert segments[0].end_time == 3.5
