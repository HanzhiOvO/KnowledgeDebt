from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.automation import AutomationRepository
from app.config import Settings
from app.database import Database
from app.main import create_app
from app.models import ProviderProfileCreate
from app.provider_registry import ProviderRegistry
from app.secrets import SecretStore


def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        ai_provider="openai",
        asr_provider="openai",
        api_key=None,
        base_url="https://provider.example/v1",
        ai_model="model-ok",
        asr_model="model-ok",
    )


def registry(tmp_path, monkeypatch, handler) -> tuple[AutomationRepository, ProviderRegistry]:
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-credential")
    repository = AutomationRepository(Database(tmp_path / "provider-test.sqlite3"))
    instance = ProviderRegistry(
        repository,
        SecretStore(None),
        settings(tmp_path),
        http_transport=httpx.MockTransport(handler),
    )
    instance.ensure_environment_profiles()
    return repository, instance


def test_environment_ai_profile_refreshes_and_becomes_default_after_key_is_added(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    repository = AutomationRepository(Database(tmp_path / "provider-refresh.sqlite3"))
    initial = settings(tmp_path)
    ProviderRegistry(repository, SecretStore(None), initial).ensure_environment_profiles()
    assert repository.get_provider_defaults()["ai"]["adapter"] == "local_rule"

    monkeypatch.setenv("OPENAI_API_KEY", "server-only-key")
    changed = Settings(
        data_dir=tmp_path / "data",
        ai_provider="opencode",
        asr_provider="openai",
        api_key="server-only-key",
        base_url="https://opencode.ai/zen/go/v1",
        ai_model="deepseek-v4-flash",
        asr_model="gpt-4o-mini-transcribe",
    )
    ProviderRegistry(repository, SecretStore(None), changed).ensure_environment_profiles()

    default_ai = repository.get_provider_defaults()["ai"]
    assert default_ai["name"] == "环境变量 · AI"
    assert default_ai["vendor"] == "opencode"
    assert default_ai["base_url"] == "https://opencode.ai/zen/go/v1"
    assert default_ai["default_model"] == "deepseek-v4-flash"
    assert default_ai["credential_configured"] is True

    repository.update_provider_test(default_ai["id"], "succeeded", "server configured")
    ProviderRegistry(repository, SecretStore(None), changed).ensure_environment_profiles()
    assert repository.get_provider_profile(default_ai["id"])["last_test_status"] == "succeeded"

    local_again = Settings(
        data_dir=tmp_path / "data",
        ai_provider="local_rule",
        asr_provider="openai",
        api_key=None,
        base_url="https://opencode.ai/zen/go/v1",
        ai_model="deepseek-v4-flash",
        asr_model="gpt-4o-mini-transcribe",
    )
    monkeypatch.delenv("OPENAI_API_KEY")
    ProviderRegistry(repository, SecretStore(None), local_again).ensure_environment_profiles()
    assert repository.get_provider_defaults()["ai"]["adapter"] == "local_rule"


def test_external_connection_verifies_auth_model_and_declared_text_capability(
    tmp_path, monkeypatch
):
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        assert request.headers["authorization"] == "Bearer test-only-credential"
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "model-ok"}]})
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
        return httpx.Response(404)

    repository, instance = registry(tmp_path, monkeypatch, handler)
    profile = repository.get_provider_defaults()["ai"]
    tested = asyncio.run(instance.test_connection(profile["id"]))

    assert tested["last_test_status"] == "succeeded"
    assert "鉴权" in tested["last_test_message"]
    assert "模型“model-ok”" in tested["last_test_message"]
    assert "文本生成" in tested["last_test_message"]
    assert requests == ["/v1/models", "/v1/chat/completions"]
    call = repository.provider_usage()["items"][0]
    assert call["request_count"] == 2
    assert call["cost_known"] is False


def test_external_connection_rejects_missing_model_without_capability_call(tmp_path, monkeypatch):
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": "another-model"}]})

    repository, instance = registry(tmp_path, monkeypatch, handler)
    profile = repository.get_provider_defaults()["ai"]
    tested = asyncio.run(instance.test_connection(profile["id"]))

    assert tested["last_test_status"] == "failed"
    assert "找不到“model-ok”" in tested["last_test_message"]
    assert requests == ["/v1/models"]


def test_external_connection_tests_embedding_and_asr_payloads(tmp_path, monkeypatch):
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        assert request.headers["x-tenant-id"] == "campus-a"
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "model-ok"}]})
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})
        if request.url.path.endswith("/audio/transcriptions"):
            assert b'filename="connection-test.wav"' in request.content
            assert request.content.count(b"\x00\x00") > 100
            return httpx.Response(200, json={"text": "", "segments": []})
        return httpx.Response(404)

    repository, instance = registry(tmp_path, monkeypatch, handler)
    profile = repository.create_provider_profile(
        {
            "name": "能力组合测试",
            "vendor": "custom_openai_compatible",
            "adapter": "openai_compatible",
            "base_url": "https://provider.example/v1",
            "credential_reference": "env:OPENAI_API_KEY",
            "default_model": "model-ok",
            "capabilities": ["embeddings", "audio_transcription"],
            "custom_headers": {"X-Tenant-ID": "campus-a"},
            "external": True,
        }
    )
    tested = asyncio.run(instance.test_connection(profile["id"]))

    assert tested["last_test_status"] == "succeeded"
    assert "向量、语音转写" in tested["last_test_message"]
    assert requests == ["/v1/models", "/v1/embeddings", "/v1/audio/transcriptions"]
    assert tested["custom_headers"] == {"X-Tenant-ID": "campus-a"}


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer plaintext-secret"},
        {"X-API-Key": "plaintext-secret"},
        {"X-Tenant-ID": "tenant-a\r\nX-Injected: yes"},
    ],
)
def test_custom_headers_reject_credentials_and_header_injection(headers):
    with pytest.raises(ValidationError):
        ProviderProfileCreate(
            name="不安全请求头",
            vendor="custom_openai_compatible",
            base_url="https://provider.example/v1",
            default_model="model-ok",
            custom_headers=headers,
        )


@pytest.mark.parametrize(
    ("error_type", "expected"),
    [
        (httpx.ConnectError, "无法连接 Provider"),
        (httpx.ReadTimeout, "连接测试超时"),
    ],
)
def test_connection_network_errors_are_safe_actionable_chinese(
    tmp_path, monkeypatch, error_type, expected
):
    def handler(request: httpx.Request) -> httpx.Response:
        raise error_type("internal-endpoint-detail", request=request)

    repository, instance = registry(tmp_path, monkeypatch, handler)
    profile = repository.get_provider_defaults()["ai"]
    tested = asyncio.run(instance.test_connection(profile["id"]))

    assert tested["last_test_status"] == "failed"
    assert expected in tested["last_test_message"]
    assert "internal-endpoint-detail" not in tested["last_test_message"]


@pytest.mark.parametrize(
    "base_url",
    [
        "http://provider.example/v1",
        "https://user:password@provider.example/v1",
        "https://provider.example/v1?api_key=plaintext",
    ],
)
def test_external_profile_refuses_insecure_or_credential_bearing_base_url(
    tmp_path, monkeypatch, base_url
):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-credential")
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post(
            "/settings/providers",
            json={
                "name": "不安全接口",
                "vendor": "custom_openai_compatible",
                "adapter": "openai_compatible",
                "base_url": base_url,
                "credential_reference": "env:OPENAI_API_KEY",
                "default_model": "model-ok",
                "capabilities": ["chat_analysis"],
                "external": False,
            },
        )
    assert response.status_code == 422


def test_external_profile_normalizes_https_url_and_cannot_be_marked_local(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-credential")
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post(
            "/settings/providers",
            json={
                "name": "安全兼容接口",
                "vendor": "custom_openai_compatible",
                "adapter": "openai_compatible",
                "base_url": "https://provider.example/v1/",
                "credential_reference": "env:OPENAI_API_KEY",
                "default_model": "model-ok",
                "capabilities": ["chat_analysis"],
                "external": False,
            },
        )
    assert response.status_code == 201
    assert response.json()["base_url"] == "https://provider.example/v1"
    assert response.json()["external"] is True


def test_request_validation_never_echoes_submitted_provider_secret(tmp_path):
    sensitive_value = "sensitive-value-that-must-never-be-echoed"
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post(
            "/settings/providers",
            json={
                "name": "重复凭据来源",
                "vendor": "custom_openai_compatible",
                "adapter": "openai_compatible",
                "base_url": "https://provider.example/v1",
                "credential": sensitive_value,
                "credential_reference": "env:OPENAI_API_KEY",
                "default_model": "model-ok",
                "capabilities": ["chat_analysis"],
            },
        )

    assert response.status_code == 422
    serialized = response.text
    assert sensitive_value not in serialized
    assert "credential_reference" not in serialized
    assert response.json()["detail"].startswith("请求内容格式不正确")


def test_provider_material_change_invalidates_stale_connection_result(tmp_path):
    repository = AutomationRepository(Database(tmp_path / "provider-state.sqlite3"))
    profile = repository.create_provider_profile(
        {
            "name": "需重新验证的接口",
            "vendor": "custom_openai_compatible",
            "adapter": "openai_compatible",
            "base_url": "https://provider.example/v1",
            "default_model": "model-a",
            "capabilities": ["chat_analysis"],
            "external": True,
        }
    )
    tested = repository.update_provider_test(profile["id"], "succeeded", "连接成功")
    assert tested["last_tested_at"] is not None

    renamed = repository.update_provider_profile(profile["id"], {"name": "仅改显示名称"})
    assert renamed["last_test_status"] == "succeeded"

    changed = repository.update_provider_profile(profile["id"], {"default_model": "model-b"})
    assert changed["last_test_status"] is None
    assert changed["last_test_message"] is None
    assert changed["last_tested_at"] is None
