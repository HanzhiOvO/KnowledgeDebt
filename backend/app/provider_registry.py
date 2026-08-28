from __future__ import annotations

import asyncio
import io
import time
import wave
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from .automation import AutomationRepository
from .config import Settings
from .provider_headers import normalize_external_base_url, validate_custom_headers
from .providers.hash_embedding import HashEmbeddingProvider
from .providers.local_asr import (
    LOCAL_SERVICE_ADAPTER,
    WHISPER_CPP_ADAPTER,
    LocalOpenAICompatibleASRProvider,
    LocalWhisperCppProvider,
    WhisperCppRuntime,
    effective_whisper_threads,
    resolve_executable,
    resolve_model_path,
    resolve_vad_model_path,
)
from .providers.local_rule import LocalRuleProvider
from .providers.openai_compatible import OpenAICompatibleProvider
from .secrets import SecretStore


class LoggedAIProvider:
    """Records AI call metadata without retaining prompts, answers, or evidence payloads."""

    def __init__(self, provider: object, profile: dict[str, Any], repository: AutomationRepository):
        self.provider = provider
        self.profile = profile
        self.repository = repository

    @property
    def requires_external_upload(self) -> bool:
        return bool(getattr(self.provider, "requires_external_upload", False))

    async def _call(self, operation: str, session_id: str | None, request: Callable[[], Awaitable[Any]]) -> Any:
        started = time.monotonic()
        try:
            result = await request()
        except Exception as exc:
            self._log(operation, session_id, "failed", started, type(exc).__name__)
            raise
        self._log(operation, session_id, "succeeded", started)
        return result

    def _log(
        self,
        operation: str,
        session_id: str | None,
        status: str,
        started: float,
        error_type: str | None = None,
    ) -> None:
        self.repository.log_provider_call(
            {
                "operation": operation,
                "provider_profile_id": self.profile.get("id"),
                "provider_name": self.profile.get("name", "injected provider"),
                "model": self.profile.get("default_model"),
                "session_id": session_id,
                "status": status,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "request_count": 1,
                "cost_known": False,
                "error_type": error_type,
            }
        )

    async def analyze_session(self, session: dict, evidence: list[dict]) -> Any:
        return await self._call(
            "analysis",
            session.get("id"),
            lambda: self.provider.analyze_session(session, evidence),
        )

    async def generate_questions(
        self, session: dict, evidence: list[dict], knowledge_points: list[dict]
    ) -> Any:
        return await self._call(
            "assessment_generation",
            session.get("id"),
            lambda: self.provider.generate_questions(session, evidence, knowledge_points),
        )

    async def evaluate_answer(self, question: dict, answer: str, evidence: list[dict]) -> Any:
        return await self._call(
            "answer_evaluation",
            question.get("session_id"),
            lambda: self.provider.evaluate_answer(question, answer, evidence),
        )

    async def remediate(self, knowledge_point: dict, reason: str, evidence: list[dict]) -> Any:
        return await self._call(
            "remediation",
            knowledge_point.get("source_session_id"),
            lambda: self.provider.remediate(knowledge_point, reason, evidence),
        )


class LoggedEmbeddingProvider:
    """Records embedding call metadata without retaining text or vectors."""

    def __init__(self, provider: object, profile: dict[str, Any], repository: AutomationRepository):
        self.provider = provider
        self.profile = profile
        self.repository = repository

    @property
    def requires_external_upload(self) -> bool:
        return bool(getattr(self.provider, "requires_external_upload", False))

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        started = time.monotonic()
        try:
            result = await self.provider.embed_texts(texts)
        except Exception as exc:
            self._log("failed", started, type(exc).__name__)
            raise
        self._log("succeeded", started)
        return result

    def _log(self, status: str, started: float, error_type: str | None = None) -> None:
        self.repository.log_provider_call(
            {
                "operation": "embedding",
                "provider_profile_id": self.profile.get("id"),
                "provider_name": self.profile.get("name", "injected provider"),
                "model": self.profile.get("default_model"),
                "status": status,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "request_count": 1,
                "cost_known": False,
                "error_type": error_type,
            }
        )

PROVIDER_CATALOG: list[dict[str, Any]] = [
    {
        "vendor": "local_rule",
        "label": "本地规则引擎",
        "adapter": "local_rule",
        "groups": ["ai"],
        "capabilities": ["structured_generation", "chat_analysis"],
        "implementation_status": "tested",
        "note": (
            "零配置、完全离线的透明规则引擎。它只整理已检索到的真实资料，"
            "所有结果均标为推断，不冒充语义大模型。"
        ),
    },
    {
        "vendor": "openai",
        "label": "OpenAI",
        "adapter": "openai_compatible",
        "groups": ["ai", "asr", "embedding"],
        "capabilities": [
            "structured_generation",
            "chat_analysis",
            "embeddings",
            "audio_transcription",
            "segment_timestamps",
        ],
        "implementation_status": "tested_by_contract",
        "note": "OpenAI 接口与自定义 OpenAI-compatible 基线适配器。真实调用需用户配置并授权。",
    },
    {
        "vendor": "anthropic",
        "label": "Anthropic",
        "adapter": "anthropic_native",
        "groups": ["ai"],
        "capabilities": ["structured_generation", "chat_analysis"],
        "implementation_status": "interface_slot",
        "note": "已保留原生能力槽位；当前版本未完成真实账户验证，不会宣称可用。",
    },
    {
        "vendor": "google_gemini",
        "label": "Google Gemini",
        "adapter": "gemini_native",
        "groups": ["ai"],
        "capabilities": ["structured_generation", "chat_analysis"],
        "implementation_status": "interface_slot",
        "note": "已保留原生能力槽位；当前版本未完成真实账户验证。",
    },
    {
        "vendor": "qwen_dashscope",
        "label": "通义千问 / DashScope",
        "adapter": "openai_compatible",
        "groups": ["ai"],
        "capabilities": ["structured_generation", "chat_analysis"],
        "implementation_status": "compatible_preset_unverified",
        "note": "仅作为 OpenAI-compatible 预设；须自行测试模型的结构化输出能力。",
    },
    {
        "vendor": "deepseek",
        "label": "DeepSeek",
        "adapter": "openai_compatible",
        "groups": ["ai"],
        "capabilities": ["structured_generation", "chat_analysis"],
        "implementation_status": "compatible_preset_unverified",
        "note": "兼容预设，不等于所有模型均支持严格 JSON Schema。",
    },
    {
        "vendor": "opencode",
        "label": "OpenCode Zen",
        "adapter": "openai_compatible",
        "groups": ["ai"],
        "capabilities": ["structured_generation", "chat_analysis"],
        "implementation_status": "compatible_preset_unverified",
        "note": "OpenAI-compatible 预设；真实模型与结构化输出能力必须由用户连接测试确认。",
    },
    {
        "vendor": "moonshot",
        "label": "Kimi / Moonshot",
        "adapter": "openai_compatible",
        "groups": ["ai"],
        "capabilities": ["structured_generation", "chat_analysis"],
        "implementation_status": "compatible_preset_unverified",
        "note": "兼容预设，真实模型能力需连接测试。",
    },
    {
        "vendor": "zhipu_glm",
        "label": "智谱 GLM",
        "adapter": "openai_compatible",
        "groups": ["ai"],
        "capabilities": ["structured_generation", "chat_analysis"],
        "implementation_status": "compatible_preset_unverified",
        "note": "兼容预设，真实模型能力需连接测试。",
    },
    {
        "vendor": "minimax",
        "label": "MiniMax",
        "adapter": "openai_compatible",
        "groups": ["ai"],
        "capabilities": ["structured_generation", "chat_analysis"],
        "implementation_status": "compatible_preset_unverified",
        "note": "兼容预设，真实模型能力需连接测试。",
    },
    {
        "vendor": "custom_openai_compatible",
        "label": "自定义 OpenAI-compatible",
        "adapter": "openai_compatible",
        "groups": ["ai", "asr", "embedding"],
        "capabilities": [],
        "implementation_status": "user_verified",
        "note": "能力默认全关，须由用户选择并通过连接测试；不会因能返回文本就宣称完全兼容。",
    },
    {
        "vendor": "dashscope_asr",
        "label": "DashScope 异步录音文件识别",
        "adapter": "dashscope_async_asr",
        "groups": ["asr"],
        "capabilities": ["async_audio_transcription", "segment_timestamps", "long_audio", "hotwords"],
        "implementation_status": "interface_slot",
        "note": "接口与状态能力已建模；当前版本未完成授权样本的端到端验证，保持禁用。",
    },
    {
        "vendor": "tencent_asr",
        "label": "腾讯云录音文件识别",
        "adapter": "tencent_file_asr",
        "groups": ["asr"],
        "capabilities": ["async_audio_transcription", "segment_timestamps", "long_audio"],
        "implementation_status": "interface_slot",
        "note": "接口槽位，尚未完成真实账户验证。",
    },
    {
        "vendor": "google_batch_asr",
        "label": "Google Cloud Batch Speech-to-Text",
        "adapter": "google_batch_asr",
        "groups": ["asr"],
        "capabilities": ["async_audio_transcription", "segment_timestamps", "speaker_diarization", "long_audio"],
        "implementation_status": "interface_slot",
        "note": "接口槽位，尚未完成真实账户验证。",
    },
    {
        "vendor": "local_hash",
        "label": "本地 Hash Embedding",
        "adapter": "hash",
        "groups": ["embedding"],
        "capabilities": ["embeddings"],
        "implementation_status": "tested",
        "note": "确定性本地回退，不宣称具备通用语义理解能力。",
    },
    {
        "vendor": "local_whisper_cpp",
        "label": "本地 whisper.cpp（命令行）",
        "adapter": WHISPER_CPP_ADAPTER,
        "groups": ["asr"],
        "capabilities": ["audio_transcription", "segment_timestamps"],
        "implementation_status": "local_runtime_required",
        "note": (
            "适配器已在 macOS + whisper.cpp 1.9.2 + ggml-tiny 上用真实课堂录音验证协议，"
            "原生包固定 whisper.cpp 1.9.3 并通过运行时冒烟："
            "解析 JSON 分段时间戳，支持中文语言设置，超时与取消都会终止子进程。"
            "源码模式可连接已有 whisper.cpp；原生包由构建脚本内置运行时。"
            "模型需要由用户在应用内确认下载（推荐 medium，tiny 对技术名词识别很差）；"
            "不声明 long_audio，长录音继续走本地分片流程以便断点续跑。"
        ),
    },
    {
        "vendor": "local_asr_service",
        "label": "本地 / 私网 OpenAI 兼容 ASR 服务",
        "adapter": LOCAL_SERVICE_ADAPTER,
        "groups": ["asr"],
        "capabilities": ["audio_transcription", "segment_timestamps"],
        "implementation_status": "local_runtime_required",
        "note": (
            "适配器已实现并在 127.0.0.1 上用 whisper.cpp 1.9.2 的 whisper-server 实测通过（路径 /inference）。"
            "Base URL 必须是私网地址，公网地址会被拒绝；转写路径可用 KNOWLEDGEDEBT_LOCAL_ASR_SERVICE_PATH 配置。"
            "换用其它服务（faster-whisper-server 等）时仍需操作者用真实音频验证。"
        ),
    },
]


class ProviderRegistry:
    def __init__(
        self,
        repository: AutomationRepository,
        secrets: SecretStore,
        settings: Settings,
        *,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.repository = repository
        self.secrets = secrets
        self.settings = settings
        self.http_transport = http_transport

    def ensure_environment_profiles(self) -> None:
        profiles = self.repository.list_provider_profiles()

        def ensure(
            match: Callable[[dict[str, Any]], bool],
            values: dict[str, Any],
            *,
            refresh_existing: bool = False,
        ) -> dict[str, Any]:
            existing = next((profile for profile in profiles if match(profile)), None)
            if existing is not None:
                if refresh_existing:
                    refreshable = {
                        "name",
                        "vendor",
                        "base_url",
                        "credential_reference",
                        "default_model",
                        "capabilities",
                        "external",
                        "enabled",
                    }
                    changes = {
                        key: value
                        for key, value in values.items()
                        if key in refreshable and existing.get(key) != value
                    }
                    if changes:
                        existing = self.repository.update_provider_profile(
                            existing["id"], changes
                        )
                return existing
            created = self.repository.create_provider_profile(values)
            profiles.append(created)
            return created

        local_ai = ensure(
            lambda item: item.get("adapter") == "local_rule",
            {
                "name": "本地规则引擎",
                "vendor": "local_rule",
                "adapter": "local_rule",
                "base_url": "",
                "default_model": "transparent-rules-v1",
                "capabilities": ["structured_generation", "chat_analysis"],
                "external": False,
                "enabled": True,
                "implementation_status": "tested",
            },
        )
        ai = ensure(
            lambda item: item.get("name") == "环境变量 · AI",
            {
                "name": "环境变量 · AI",
                "vendor": self.settings.ai_provider,
                "adapter": "openai_compatible",
                "base_url": self.settings.base_url,
                "credential_reference": "env:OPENAI_API_KEY",
                "default_model": self.settings.ai_model,
                "capabilities": ["structured_generation", "chat_analysis"],
                "external": True,
            },
            refresh_existing=True,
        )
        asr = ensure(
            lambda item: item.get("name") == "环境变量 · ASR",
            {
                "name": "环境变量 · ASR",
                "vendor": self.settings.asr_provider,
                "adapter": "openai_compatible",
                "base_url": self.settings.base_url,
                "credential_reference": "env:OPENAI_API_KEY",
                "default_model": self.settings.asr_model,
                "capabilities": ["audio_transcription", "segment_timestamps"],
                "external": True,
            },
            refresh_existing=True,
        )
        embedding = ensure(
            lambda item: item.get("adapter") == "hash",
            {
                "name": "本地 Hash Embedding",
                "vendor": "local_hash",
                "adapter": "hash",
                "default_model": "hash-96",
                "capabilities": ["embeddings"],
                "external": False,
            },
        )
        local_asr = ensure(
            lambda item: item.get("adapter") == WHISPER_CPP_ADAPTER,
            {
                "name": "本地 whisper.cpp",
                "vendor": "local_whisper_cpp",
                "adapter": WHISPER_CPP_ADAPTER,
                "base_url": "",
                "default_model": self.settings.local_asr_model,
                "capabilities": ["audio_transcription", "segment_timestamps"],
                "external": False,
                "enabled": True,
                "implementation_status": "local_runtime_required",
            },
        )
        defaults = self.repository.get_provider_defaults()
        environment_ai_ready = bool(
            self.settings.api_key or self.secrets.resolve(None, "env:OPENAI_API_KEY")
        )
        use_environment_ai = environment_ai_ready and self.settings.ai_provider != "local_rule"
        if "ai" not in defaults:
            self.repository.set_provider_default(
                "ai", ai["id"] if use_environment_ai else local_ai["id"]
            )
        elif use_environment_ai and defaults["ai"].get("adapter") == "local_rule":
            self.repository.set_provider_default("ai", ai["id"])
        elif not use_environment_ai and defaults["ai"].get("id") == ai["id"]:
            self.repository.set_provider_default("ai", local_ai["id"])
        if "asr" not in defaults:
            # 本地 whisper.cpp 就绪时优先本地转写：不外发、无需逐次授权。
            self.repository.set_provider_default(
                "asr", local_asr["id"] if self.local_asr_status(local_asr)["ready"] else asr["id"]
            )
        if "embedding" not in defaults:
            self.repository.set_provider_default("embedding", embedding["id"])

    def local_whisper_profile(self) -> dict[str, Any] | None:
        """Return the configured local CLI profile, preferring the active ASR route."""

        defaults = self.repository.get_provider_defaults()
        active = defaults.get("asr")
        if active and active.get("adapter") == WHISPER_CPP_ADAPTER:
            return active
        return next(
            (
                profile
                for profile in self.repository.list_provider_profiles()
                if profile.get("adapter") == WHISPER_CPP_ADAPTER
            ),
            None,
        )

    def active_local_model_filename(self) -> str | None:
        active = self.repository.get_provider_defaults().get("asr")
        if not active or active.get("adapter") != WHISPER_CPP_ADAPTER:
            return None
        return (active.get("default_model") or "").strip() or None

    def activate_local_model(self, file_name: str) -> dict[str, Any]:
        """Select a verified model and make the local CLI the explicit ASR route."""

        profile = self.local_whisper_profile()
        if profile is None:
            profile = self.repository.create_provider_profile(
                {
                    "name": "本地 whisper.cpp",
                    "vendor": "local_whisper_cpp",
                    "adapter": WHISPER_CPP_ADAPTER,
                    "base_url": "",
                    "default_model": file_name,
                    "capabilities": ["audio_transcription", "segment_timestamps"],
                    "external": False,
                    "enabled": True,
                    "implementation_status": "local_runtime_required",
                }
            )
        else:
            profile = self.repository.update_provider_profile(
                profile["id"], {"default_model": file_name, "enabled": True}
            )
        self.repository.set_provider_default("asr", profile["id"])
        return profile

    def whisper_runtime(self, profile: dict[str, Any] | None = None) -> WhisperCppRuntime:
        """Profile 可覆盖可执行文件路径与模型；其余运行参数由服务端环境统一控制。"""

        profile = profile if profile is not None else self.local_whisper_profile() or {}
        return WhisperCppRuntime(
            binary_path=(profile.get("base_url") or "").strip() or self.settings.local_asr_binary,
            model=(profile.get("default_model") or "").strip() or self.settings.local_asr_model,
            model_dir=self.settings.local_asr_model_dir,
            language=self.settings.local_asr_language,
            threads=self.settings.local_asr_threads,
            timeout_seconds=self.settings.local_asr_timeout_seconds,
            initial_prompt=self.settings.local_asr_initial_prompt,
            ffmpeg_path=self.settings.ffmpeg_path,
            vad_enabled=self.settings.local_asr_vad_enabled,
            vad_model=self.settings.local_asr_vad_model,
        )

    def local_asr_status(self, profile: dict[str, Any] | None = None) -> dict[str, Any]:
        """报告本地 whisper.cpp 的真实就绪情况，不猜测、不夸大。"""

        profile = profile if profile is not None else self.local_whisper_profile()
        runtime = self.whisper_runtime(profile)
        binary = resolve_executable(runtime.binary_path)
        model = resolve_model_path(runtime.model, runtime.model_dir)
        vad_model = resolve_vad_model_path(runtime)
        return {
            "adapter": WHISPER_CPP_ADAPTER,
            "binary": runtime.binary_path,
            "binary_ready": bool(binary),
            "binary_resolved": binary,
            "model": runtime.model,
            "model_dir": str(runtime.model_dir) if runtime.model_dir else None,
            "model_ready": bool(model),
            "model_resolved": str(model) if model else None,
            "model_bytes": model.stat().st_size if model else None,
            "language": runtime.language,
            "threads": runtime.threads,
            "effective_threads": effective_whisper_threads(runtime.threads),
            "timeout_seconds": runtime.timeout_seconds,
            "ffmpeg_ready": bool(resolve_executable(runtime.ffmpeg_path)),
            "direct_input_formats": ["FLAC", "MP3", "OGG", "WAV"],
            "vad_enabled": runtime.vad_enabled,
            "vad_ready": bool(vad_model),
            "vad_model_resolved": str(vad_model) if vad_model else None,
            "ready": bool(binary and model),
            "profile_id": profile.get("id") if profile else None,
            "active": bool(
                profile
                and self.repository.get_provider_defaults().get("asr", {}).get("id")
                == profile.get("id")
            ),
        }

    def build_local_asr(self, profile: dict[str, Any], secret: str | None) -> object:
        if profile["adapter"] == WHISPER_CPP_ADAPTER:
            return LocalWhisperCppProvider(
                self.whisper_runtime(profile),
                work_dir=self.settings.data_dir / "local-asr-scratch",
            )
        return LocalOpenAICompatibleASRProvider(
            profile["base_url"] or self.settings.local_asr_service_base_url,
            profile["default_model"] or self.settings.local_asr_service_model,
            api_key=secret,
            language=self.settings.local_asr_language,
            timeout_seconds=self.settings.local_asr_timeout_seconds,
            convert_to_wav=self.settings.local_asr_service_convert_wav,
            ffmpeg_path=self.settings.ffmpeg_path,
            work_dir=self.settings.data_dir / "local-asr-scratch",
            transcriptions_path=self.settings.local_asr_service_path,
        )

    def resolve_profile_secret(self, profile_id: str) -> tuple[dict[str, Any], str | None]:
        profile = self.repository.get_provider_profile(profile_id, include_secret=True)
        if not profile["enabled"]:
            raise ValueError("该 Provider Profile 已禁用，请先启用或选择其他 Profile。")
        secret = self.secrets.resolve(
            profile.get("credential_ciphertext"), profile.get("credential_reference")
        )
        return profile, secret

    def build(self, profile_id: str) -> object:
        profile, secret = self.resolve_profile_secret(profile_id)
        if profile["adapter"] == "local_rule":
            return LocalRuleProvider()
        if profile["adapter"] == "hash":
            return HashEmbeddingProvider()
        if profile["adapter"] in {WHISPER_CPP_ADAPTER, LOCAL_SERVICE_ADAPTER}:
            return self.build_local_asr(profile, secret)
        if profile["adapter"] != "openai_compatible":
            raise ValueError(
                f"{profile['adapter']} is an interface slot and has not been enabled by a verified adapter"
            )
        return OpenAICompatibleProvider(
            secret,
            normalize_external_base_url(profile["base_url"]),
            profile["default_model"],
            profile["default_model"],
            profile["default_model"],
            custom_headers=profile.get("custom_headers"),
        )

    def default(self, group: str) -> tuple[dict[str, Any], object]:
        defaults = self.repository.get_provider_defaults()
        if group not in defaults:
            raise ValueError(f"尚未为 {group} 配置默认 Provider Profile。")
        profile = defaults[group]
        return profile, self.build(profile["id"])

    async def test_connection(self, profile_id: str) -> dict[str, Any]:
        profile, secret = self.resolve_profile_secret(profile_id)
        started = time.monotonic()
        request_count = 0
        try:
            if profile["adapter"] == "local_rule":
                provider = LocalRuleProvider()
                await provider.analyze_session({"title": "连接测试"}, [])
                message = "本地规则引擎可用：零配置、无网络请求、不会外发课程资料。"
            elif profile["adapter"] == "hash":
                message = "本地 Hash Provider 可用"
            elif profile["adapter"] == WHISPER_CPP_ADAPTER:
                provider = self.build_local_asr(profile, secret)
                ready = await asyncio.to_thread(provider.preflight)  # type: ignore[attr-defined]
                message = (
                    f"本地 whisper.cpp 已就绪：{ready['binary']}，模型 {Path(ready['model']).name}"
                    f"（{ready['model_bytes'] / 1_048_576:.0f} MB）。"
                    "音频不会离开本机；实际识别质量仍需用真实课堂录音验证。"
                )
            elif profile["adapter"] == LOCAL_SERVICE_ADAPTER:
                provider = self.build_local_asr(profile, secret)
                message = await provider.health()  # type: ignore[attr-defined]
            elif profile["adapter"] == "openai_compatible":
                if not secret:
                    raise ValueError("鉴权信息未配置，或引用的环境变量为空。")
                headers = {
                    **validate_custom_headers(profile.get("custom_headers")),
                    "Authorization": f"Bearer {secret}",
                }
                async with httpx.AsyncClient(timeout=20, transport=self.http_transport) as client:
                    base_url = normalize_external_base_url(profile["base_url"])
                    response = await client.get(f"{base_url}/models", headers=headers)
                    request_count += 1
                    self._require_success(response, "模型列表")
                    model_payload = self._json_object(response, "模型列表")
                    model_ids = {
                        item.get("id")
                        for item in model_payload.get("data", [])
                        if isinstance(item, dict) and isinstance(item.get("id"), str)
                    }
                    model = profile.get("default_model", "").strip()
                    if not model:
                        raise ValueError("鉴权成功，但 Profile 没有填写模型 ID。")
                    if model not in model_ids:
                        raise ValueError(f"鉴权成功，但模型列表中找不到“{model}”。请核对模型 ID。")

                    capabilities = set(profile.get("capabilities", []))
                    verified: list[str] = []
                    if capabilities.intersection({"structured_generation", "chat_analysis"}):
                        response = await client.post(
                            f"{base_url}/chat/completions",
                            headers=headers,
                            json={
                                "model": model,
                                "messages": [{"role": "user", "content": "只回复 OK"}],
                                "max_tokens": 8,
                            },
                        )
                        request_count += 1
                        self._require_success(response, "文本生成能力")
                        payload = self._json_object(response, "文本生成能力")
                        if not isinstance(payload.get("choices"), list) or not payload["choices"]:
                            raise ValueError("文本生成端点成功响应，但没有返回 choices。")
                        verified.append("文本生成")
                    if "embeddings" in capabilities:
                        response = await client.post(
                            f"{base_url}/embeddings",
                            headers=headers,
                            json={"model": model, "input": ["KnowledgeDebt 连接测试"]},
                        )
                        request_count += 1
                        self._require_success(response, "向量能力")
                        payload = self._json_object(response, "向量能力")
                        if not isinstance(payload.get("data"), list) or not payload["data"]:
                            raise ValueError("向量端点成功响应，但没有返回 data。")
                        verified.append("向量")
                    if capabilities.intersection(
                        {"audio_transcription", "async_audio_transcription"}
                    ):
                        response = await client.post(
                            f"{base_url}/audio/transcriptions",
                            headers=headers,
                            data={"model": model, "response_format": "verbose_json"},
                            files={"file": ("connection-test.wav", self._silent_wav(), "audio/wav")},
                        )
                        request_count += 1
                        self._require_success(response, "语音转写能力")
                        payload = self._json_object(response, "语音转写能力")
                        if "text" not in payload and not isinstance(payload.get("segments"), list):
                            raise ValueError("转写端点成功响应，但没有返回 text 或 segments。")
                        verified.append("语音转写")
                if not verified:
                    raise ValueError("鉴权和模型存在性已验证，但 Profile 没有声明可测试的能力。")
                message = (
                    f"鉴权、模型“{model}”和{'、'.join(verified)}最小请求均通过"
                    f"（共 {request_count} 次请求）。"
                )
            else:
                raise ValueError("该原生适配器尚未完成真实验证，不能执行误导性的连接测试")
        except Exception as exc:
            message = self._friendly_connection_error(exc)
            self.repository.log_provider_call(
                {
                    "operation": "provider_connection_test",
                    "provider_profile_id": profile["id"],
                    "provider_name": profile["name"],
                    "model": profile.get("default_model"),
                    "status": "failed",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "request_count": max(1, request_count),
                    "cost_known": False,
                    "error_type": type(exc).__name__,
                }
            )
            return self.repository.update_provider_test(profile_id, "failed", message)
        self.repository.log_provider_call(
            {
                "operation": "provider_connection_test",
                "provider_profile_id": profile["id"],
                "provider_name": profile["name"],
                "model": profile.get("default_model"),
                "status": "succeeded",
                "duration_ms": int((time.monotonic() - started) * 1000),
                "request_count": max(1, request_count),
                "cost_known": False,
            }
        )
        return self.repository.update_provider_test(profile_id, "succeeded", message)

    @staticmethod
    def _require_success(response: httpx.Response, operation: str) -> None:
        if response.is_success:
            return
        if response.status_code in {401, 403}:
            raise ValueError(f"{operation}鉴权失败（HTTP {response.status_code}），请检查 API Key。")
        if response.status_code == 404:
            raise ValueError(f"{operation}端点不存在（HTTP 404），请检查 Base URL 和能力类型。")
        if response.status_code == 429:
            raise ValueError(f"{operation}被限流或配额不足（HTTP 429），请稍后重试或检查余额。")
        raise ValueError(f"{operation}返回 HTTP {response.status_code}，连接测试未通过。")

    @staticmethod
    def _json_object(response: httpx.Response, operation: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError(f"{operation}返回的不是 JSON，可能填写了错误的 Base URL。") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{operation}返回格式不兼容。")
        return payload

    @staticmethod
    def _friendly_connection_error(exc: Exception) -> str:
        if isinstance(exc, httpx.TimeoutException):
            return "连接测试超时。请检查网络、Base URL 和服务状态后重试。"
        if isinstance(exc, httpx.ConnectError):
            return "无法连接 Provider。请检查网络、Base URL、TLS 证书和本地代理设置。"
        if isinstance(exc, httpx.RequestError):
            return "Provider 请求没有完成。请检查网络与 Base URL 后重试。"
        message = str(exc).strip()
        return message or "连接测试失败，Provider 没有返回可用结果。"

    @staticmethod
    def _silent_wav() -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16_000)
            audio.writeframes(b"\x00\x00" * 8_000)
        return output.getvalue()
