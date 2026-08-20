from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from .automation import AutomationRepository
from .config import Settings
from .providers.hash_embedding import HashEmbeddingProvider
from .providers.local_asr import (
    LOCAL_SERVICE_ADAPTER,
    WHISPER_CPP_ADAPTER,
    LocalOpenAICompatibleASRProvider,
    LocalWhisperCppProvider,
    WhisperCppRuntime,
    resolve_executable,
    resolve_model_path,
)
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
            "适配器已实现并在 macOS + whisper.cpp 1.9.2 + ggml-tiny 上用真实课堂录音实测通过："
            "解析 JSON 分段时间戳，支持中文语言设置，超时与取消都会终止子进程。"
            "需要操作者自行安装 whisper.cpp 并下载 ggml 模型（推荐 medium，tiny 对技术名词识别很差）；"
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
    ):
        self.repository = repository
        self.secrets = secrets
        self.settings = settings

    def ensure_environment_profiles(self) -> None:
        if self.repository.list_provider_profiles():
            return
        ai = self.repository.create_provider_profile(
            {
                "name": "环境变量 · AI",
                "vendor": self.settings.ai_provider,
                "adapter": "openai_compatible",
                "base_url": self.settings.base_url,
                "credential_reference": "env:OPENAI_API_KEY",
                "default_model": self.settings.ai_model,
                "capabilities": ["structured_generation", "chat_analysis"],
                "external": True,
            }
        )
        asr = self.repository.create_provider_profile(
            {
                "name": "环境变量 · ASR",
                "vendor": self.settings.asr_provider,
                "adapter": "openai_compatible",
                "base_url": self.settings.base_url,
                "credential_reference": "env:OPENAI_API_KEY",
                "default_model": self.settings.asr_model,
                "capabilities": ["audio_transcription", "segment_timestamps"],
                "external": True,
            }
        )
        embedding = self.repository.create_provider_profile(
            {
                "name": "本地 Hash Embedding",
                "vendor": "local_hash",
                "adapter": "hash",
                "default_model": "hash-96",
                "capabilities": ["embeddings"],
                "external": False,
            }
        )
        local_asr = self.repository.create_provider_profile(
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
            }
        )
        self.repository.set_provider_default("ai", ai["id"])
        # 本地 whisper.cpp 就绪时优先本地转写：不外发、无需逐次授权。
        self.repository.set_provider_default(
            "asr", local_asr["id"] if self.local_asr_status()["ready"] else asr["id"]
        )
        self.repository.set_provider_default("embedding", embedding["id"])

    def whisper_runtime(self, profile: dict[str, Any] | None = None) -> WhisperCppRuntime:
        """Profile 可覆盖可执行文件路径与模型；其余运行参数由服务端环境统一控制。"""

        profile = profile or {}
        return WhisperCppRuntime(
            binary_path=(profile.get("base_url") or "").strip() or self.settings.local_asr_binary,
            model=(profile.get("default_model") or "").strip() or self.settings.local_asr_model,
            model_dir=self.settings.local_asr_model_dir,
            language=self.settings.local_asr_language,
            threads=self.settings.local_asr_threads,
            timeout_seconds=self.settings.local_asr_timeout_seconds,
            initial_prompt=self.settings.local_asr_initial_prompt,
            ffmpeg_path=self.settings.ffmpeg_path,
        )

    def local_asr_status(self, profile: dict[str, Any] | None = None) -> dict[str, Any]:
        """报告本地 whisper.cpp 的真实就绪情况，不猜测、不夸大。"""

        runtime = self.whisper_runtime(profile)
        binary = resolve_executable(runtime.binary_path)
        model = resolve_model_path(runtime.model, runtime.model_dir)
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
            "timeout_seconds": runtime.timeout_seconds,
            "ffmpeg_ready": bool(resolve_executable(runtime.ffmpeg_path)),
            "ready": bool(binary and model),
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
            raise ValueError("provider profile is disabled")
        secret = self.secrets.resolve(
            profile.get("credential_ciphertext"), profile.get("credential_reference")
        )
        return profile, secret

    def build(self, profile_id: str) -> object:
        profile, secret = self.resolve_profile_secret(profile_id)
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
            profile["base_url"],
            profile["default_model"],
            profile["default_model"],
            profile["default_model"],
        )

    def default(self, group: str) -> tuple[dict[str, Any], object]:
        defaults = self.repository.get_provider_defaults()
        if group not in defaults:
            raise ValueError(f"no default {group} provider profile is configured")
        profile = defaults[group]
        return profile, self.build(profile["id"])

    async def test_connection(self, profile_id: str) -> dict[str, Any]:
        profile, secret = self.resolve_profile_secret(profile_id)
        started = time.monotonic()
        try:
            if profile["adapter"] == "hash":
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
                    raise ValueError("credential is not configured or referenced environment variable is empty")
                headers = {"Authorization": f"Bearer {secret}"}
                async with httpx.AsyncClient(timeout=20) as client:
                    response = await client.get(f"{profile['base_url'].rstrip('/')}/models", headers=headers)
                if not response.is_success:
                    raise ValueError(f"provider returned HTTP {response.status_code}")
                message = "连接成功；具体模型的结构化输出与转写能力仍需实际任务验证"
            else:
                raise ValueError("该原生适配器尚未完成真实验证，不能执行误导性的连接测试")
        except Exception as exc:
            self.repository.log_provider_call(
                {
                    "operation": "provider_connection_test",
                    "provider_profile_id": profile["id"],
                    "provider_name": profile["name"],
                    "model": profile.get("default_model"),
                    "status": "failed",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "request_count": 1,
                    "cost_known": False,
                    "error_type": type(exc).__name__,
                }
            )
            return self.repository.update_provider_test(profile_id, "failed", str(exc))
        self.repository.log_provider_call(
            {
                "operation": "provider_connection_test",
                "provider_profile_id": profile["id"],
                "provider_name": profile["name"],
                "model": profile.get("default_model"),
                "status": "succeeded",
                "duration_ms": int((time.monotonic() - started) * 1000),
                "request_count": 1,
                "cost_known": False,
            }
        )
        return self.repository.update_provider_test(profile_id, "succeeded", message)
