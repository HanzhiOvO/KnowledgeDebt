from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

from cryptography.fernet import Fernet
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
        encryption_key=Fernet.generate_key().decode("ascii"),
    )
    return TestClient(create_app(settings))


def test_one_key_deepseek_configuration_persists_without_leaking_key(tmp_path: Path):
    client = make_client(tmp_path)

    catalog = client.get("/settings/providers/catalog").json()
    vendors = {item["vendor"] for item in catalog}
    assert {"local_rule", "openai", "deepseek", "anthropic", "opencode"} <= vendors

    response = client.post(
        "/settings/providers",
        json={
            "name": "我的 DeepSeek",
            "vendor": "deepseek",
            "adapter": "openai_compatible",
            "base_url": "https://api.deepseek.com/",
            "credential": "sk-abcdef1234567890",
            "default_model": "deepseek-chat",
            "capabilities": ["structured_generation", "chat_analysis"],
            "external": True,
        },
    )
    assert response.status_code == 201, response.text
    profile = response.json()
    assert profile["vendor"] == "deepseek"
    assert profile["base_url"] == "https://api.deepseek.com"
    assert profile["credential_configured"] is True
    assert "sk-abcdef1234567890" not in response.text

    stored, secret = client.app.state.provider_registry.resolve_profile_secret(profile["id"])
    assert stored["credential_ciphertext"] != "sk-abcdef1234567890"
    assert secret == "sk-abcdef1234567890"
    assert not (tmp_path / "runtime-provider.json").exists()

    routed = client.put("/settings/providers/defaults/ai", json={"profile_id": profile["id"]})
    assert routed.status_code == 200
    assert client.get("/settings/provider").json()["defaults"]["ai"]["id"] == profile["id"]


def test_unverified_anthropic_stays_disabled_and_opencode_uses_secure_profile(tmp_path: Path):
    client = make_client(tmp_path)

    anthropic = client.post(
        "/settings/providers",
        json={
            "name": "Anthropic",
            "vendor": "anthropic",
            "adapter": "anthropic_native",
            "base_url": "https://api.anthropic.com",
            "credential_reference": "env:ANTHROPIC_API_KEY",
            "default_model": "claude-sonnet-4-5",
            "capabilities": ["structured_generation", "chat_analysis"],
            "external": True,
            "enabled": True,
        },
    )
    assert anthropic.status_code == 422
    assert "接口槽位" in anthropic.json()["detail"]

    opencode = client.post(
        "/settings/providers",
        json={
            "name": "OpenCode Zen",
            "vendor": "opencode",
            "adapter": "openai_compatible",
            "base_url": "https://opencode.ai/zen/v1",
            "credential_reference": "env:OPENCODE_API_KEY",
            "default_model": "gpt-5.5",
            "capabilities": ["structured_generation", "chat_analysis"],
            "external": True,
        },
    )
    assert opencode.status_code == 201
    assert opencode.json()["vendor"] == "opencode"
    assert opencode.json()["credential_reference"] == "env:OPENCODE_API_KEY"
    assert "oc-test-key" not in opencode.text


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
