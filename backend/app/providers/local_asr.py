"""本地 ASR 适配器：whisper.cpp 命令行与局域网 OpenAI 兼容转写服务。

设计边界：
- 只处理已经保存到本机或私网的媒体，永远不把音频发往公网；
- 只声明真实实现的能力（分段时间戳、中文语言设置、超时与取消），不声明未验证的说话人分离或热词；
- 失败、超时、取消都必须保留原始文件与已成功分片，错误信息面向中文用户且可执行。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import mimetypes
import os
import shutil
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..models import TranscriptSegment
from .base import ProviderNotConfigured, ProviderOutputError, ProviderRequestError

WHISPER_CPP_ADAPTER = "local_whisper_cpp"
LOCAL_SERVICE_ADAPTER = "local_openai_asr"
LOCAL_ASR_ADAPTERS = frozenset({WHISPER_CPP_ADAPTER, LOCAL_SERVICE_ADAPTER})

#: 当前锁定的 whisper.cpp 可直接解码这些容器。MediaPreparer 生成的 FLAC
#: 本身已是 16kHz 单声道，不应再解码、写出一份临时 WAV。
WHISPER_CPP_NATIVE_SUFFIXES = frozenset({".flac", ".mp3", ".ogg", ".wav"})
#: whisper.cpp 不直接支持的容器仍统一转为 16kHz 单声道 16bit WAV。
WHISPER_CPP_INPUT_SUFFIX = ".wav"
DEFAULT_VAD_MODEL_FILE = "ggml-silero-v6.2.0.bin"
#: OpenAI 兼容默认路径；whisper.cpp 自带 server 需要改成 /inference。
DEFAULT_TRANSCRIPTIONS_PATH = "/audio/transcriptions"
_TERMINATE_GRACE_SECONDS = 5.0
_PRIVATE_HOST_SUFFIXES = (".local", ".internal", ".lan", ".home", ".home.arpa", ".ts.net")
_CARRIER_GRADE_NAT = ip_network("100.64.0.0/10")


class LocalASRUnavailable(ProviderNotConfigured):
    """本地 ASR 运行时缺失或未配置；属于可修复的配置问题，不是媒体损坏。"""


class LocalASRTimeout(ProviderRequestError):
    """本地 ASR 超过配置的墙钟预算，已终止子进程。"""


def is_local_endpoint(base_url: str) -> bool:
    """仅当地址位于本机、私网、Tailscale 私网或局域网主机名时返回 True。"""

    host = (urlsplit(base_url).hostname or "").strip().lower()
    if not host:
        return False
    if host in {"localhost", "localhost.localdomain"}:
        return True
    if host.endswith(_PRIVATE_HOST_SUFFIXES):
        return True
    try:
        address = ip_address(host)
    except ValueError:
        # 没有点号的裸主机名只能在局域网内解析，例如 dorm-server。
        return "." not in host
    if address.version == 4 and address in _CARRIER_GRADE_NAT:
        return True
    return address.is_loopback or address.is_private or address.is_link_local


def assert_local_endpoint(base_url: str) -> str:
    """校验并规范化本地 ASR 服务地址；公网地址一律拒绝，避免把外发伪装成本地。"""

    candidate = (base_url or "").strip()
    if not candidate:
        raise ValueError("本地 ASR 服务需要 Base URL，例如 http://127.0.0.1:8080/v1")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("本地 ASR 服务的 Base URL 格式无效。") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("本地 ASR 服务的 Base URL 必须以 http:// 或 https:// 开头")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Base URL 不能包含用户名或密码；请使用加密密钥字段。")
    if parsed.query or parsed.fragment:
        raise ValueError("Base URL 不能包含查询参数或片段，避免把 Token 写入普通配置。")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Base URL 端口必须在 1 到 65535 之间。")
    normalized = urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, parsed.path.rstrip("/"), "", "")
    )
    if not is_local_endpoint(normalized):
        raise ValueError(
            "该 Base URL 指向公网地址，不能声明为本地 ASR。"
            "本地适配器只允许 localhost、127.0.0.1、私网地址（10./172.16-31./192.168.）、"
            "Tailscale 100.64.0.0/10 或 .local/.lan/.internal 主机名。"
        )
    return normalized


def resolve_executable(candidate: str) -> str | None:
    """把可执行文件名或路径解析为真实路径；解析失败返回 None，由调用方给出中文提示。"""

    if not candidate:
        return None
    found = shutil.which(candidate)
    if found:
        return found
    expanded = Path(candidate).expanduser()
    return str(expanded) if expanded.is_file() else None


def resolve_model_path(model: str, model_dir: Path | None) -> Path | None:
    """支持绝对路径、模型目录下的文件名，以及 `medium` 这类简称。"""

    if not model:
        return None
    candidate = Path(model).expanduser()
    if candidate.is_file():
        return candidate
    if candidate.is_absolute():
        return None
    if model_dir:
        for option in (model_dir / candidate, model_dir / f"ggml-{model}.bin"):
            if option.is_file():
                return option
    return None


def effective_whisper_threads(configured: int, cpu_count: int | None = None) -> int:
    """返回 whisper.cpp 实际会使用的线程数。

    CLI 的默认值是“逻辑核心数和 4 的较小值”。沿用上游策略可以避免在
    双核低配机器上过度订阅，也不会通过改小 beam/best-of 换取速度。
    """

    if configured > 0:
        return configured
    available = cpu_count if cpu_count is not None else os.cpu_count()
    return max(1, min(4, available or 1))


def clock_to_seconds(value: Any) -> float | None:
    """解析 whisper.cpp 的 `00:01:02,500` 时间戳；无法解析时返回 None。"""

    if not isinstance(value, str) or ":" not in value:
        return None
    head, _, milliseconds = value.strip().partition(",")
    parts = head.split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = (int(part) for part in parts)
        fraction = int(milliseconds or 0) / 1000
    except ValueError:
        return None
    return hours * 3600 + minutes * 60 + seconds + fraction


def segments_from_verbose_json(payload: dict[str, Any]) -> list[TranscriptSegment]:
    """解析 OpenAI 兼容 `verbose_json` 转写结果，保留真实分段时间戳。"""

    raw = payload.get("segments") or []
    segments: list[TranscriptSegment] = []
    for item in raw:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        start = float(item.get("start", 0) or 0)
        end = float(item.get("end", start) or start)
        segments.append(TranscriptSegment(start_time=start, end_time=max(start, end), text=text))
    if segments:
        return segments
    whole = (payload.get("text") or "").strip()
    if not whole:
        return []
    duration = float(payload.get("duration", 0) or 0)
    return [TranscriptSegment(start_time=0, end_time=max(0.0, duration), text=whole)]


async def terminate_process(process: asyncio.subprocess.Process) -> None:
    """先 SIGTERM 再 SIGKILL，保证取消或超时后不留下本地转写进程。"""

    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    waiter = asyncio.ensure_future(process.wait())
    try:
        await asyncio.wait_for(asyncio.shield(waiter), timeout=_TERMINATE_GRACE_SECONDS)
    except (TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()


async def run_process(command: list[str], *, timeout: int, failure: str) -> str:
    """运行本地命令；超时与取消都会真正终止子进程，错误信息保留真实 stderr。"""

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        await terminate_process(process)
        raise LocalASRTimeout(
            f"{failure}：超过 {timeout} 秒仍未完成，已终止本地进程。"
            "原始文件与已成功分片都已保留；可调小 KNOWLEDGEDEBT_TRANSCRIPTION_CHUNK_SECONDS、"
            "改用更小模型或提高 KNOWLEDGEDEBT_LOCAL_ASR_TIMEOUT_SECONDS 后重试。"
        ) from None
    except asyncio.CancelledError:
        await terminate_process(process)
        raise
    if process.returncode != 0:
        detail = (stderr or b"").decode("utf-8", "replace").strip()[-600:]
        raise ProviderRequestError(f"{failure}：{detail or f'退出码 {process.returncode}'}")
    return (stdout or b"").decode("utf-8", "replace")


async def ensure_wav_16k(
    source: Path,
    workspace: Path,
    ffmpeg_path: str,
    timeout: int,
    *,
    force: bool = False,
) -> Path:
    """把 whisper.cpp 不直接支持的分片转为 16kHz 单声道 WAV。

    FLAC/MP3/OGG/WAV 由 whisper.cpp 直接解码，避免低配机器上重复解码和临时 WAV I/O。
    该函数在任何情况下都不会改动原文件。
    """

    if not force and source.suffix.lower() in WHISPER_CPP_NATIVE_SUFFIXES:
        return source
    ffmpeg = resolve_executable(ffmpeg_path)
    if not ffmpeg:
        raise LocalASRUnavailable(
            "本地 ASR 需要 16kHz 单声道 WAV，需要 FFmpeg 转换当前分片，但未找到 FFmpeg。"
            "请安装 FFmpeg 或设置 KNOWLEDGEDEBT_FFMPEG_PATH；原始文件与已成功分片都已保留。"
        )
    target = workspace / f"{source.stem}-16k{WHISPER_CPP_INPUT_SUFFIX}"
    await run_process(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(target),
        ],
        timeout=max(120, min(timeout, 1800)),
        failure="FFmpeg 无法把该分片转换为本地 ASR 需要的 16kHz 单声道 WAV",
    )
    if not target.is_file() or target.stat().st_size == 0:
        raise ProviderRequestError(
            "FFmpeg 输出的 WAV 为空，可能是该分片没有可解码音频轨；原始文件已保留，可重新准备分片后重试。"
        )
    return target


@dataclass(frozen=True)
class WhisperCppRuntime:
    """whisper.cpp 运行参数；模型与可执行文件均在调用前实时校验。"""

    binary_path: str = "whisper-cli"
    model: str = ""
    model_dir: Path | None = None
    language: str = "zh"
    threads: int = 0
    timeout_seconds: int = 3600
    initial_prompt: str = ""
    ffmpeg_path: str = "ffmpeg"
    vad_enabled: bool = True
    vad_model: str = ""
    extra_args: tuple[str, ...] = ()


def resolve_vad_model_path(runtime: WhisperCppRuntime) -> Path | None:
    """解析可选 VAD 模型；缺失时保持完整音频转写，不阻断核心功能。"""

    if not runtime.vad_enabled:
        return None
    if runtime.vad_model:
        candidate = resolve_model_path(runtime.vad_model, runtime.model_dir)
        return candidate if candidate and candidate.stat().st_size > 0 else None
    if runtime.model_dir:
        candidate = runtime.model_dir / DEFAULT_VAD_MODEL_FILE
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


class LocalWhisperCppProvider:
    """whisper.cpp 命令行的真实适配器（`whisper-cli`，旧版为 `main`）。

    不声明 long_audio：长录音由 MediaPreparer 分片，才能限制内存、跳过成功分片并断点续跑。
    """

    requires_external_upload = False

    def __init__(self, runtime: WhisperCppRuntime, work_dir: Path | None = None):
        self.runtime = runtime
        self.work_dir = work_dir

    def preflight(self) -> dict[str, Any]:
        """校验可执行文件与模型是否真实可用，返回可展示的就绪信息。"""

        binary = resolve_executable(self.runtime.binary_path)
        if not binary:
            raise LocalASRUnavailable(
                f"未找到 whisper.cpp 可执行文件“{self.runtime.binary_path or '未配置'}”。"
                "请安装 whisper.cpp（提供 whisper-cli），并设置 KNOWLEDGEDEBT_LOCAL_ASR_BINARY "
                "或在 Profile 的可执行文件字段填写绝对路径。原始录音已保留，配置后可直接重试。"
            )
        model = resolve_model_path(self.runtime.model, self.runtime.model_dir)
        if not model:
            location = str(self.runtime.model_dir) if self.runtime.model_dir else "未配置模型目录"
            raise LocalASRUnavailable(
                f"未找到 whisper.cpp 模型“{self.runtime.model or '未配置'}”（已查找：{location}）。"
                "请下载 ggml 模型并设置 KNOWLEDGEDEBT_LOCAL_ASR_MODEL 或在 Profile 的默认模型字段填写路径。"
                "原始录音已保留，配置后可直接重试。"
            )
        size = model.stat().st_size
        if size == 0:
            raise LocalASRUnavailable(
                f"whisper.cpp 模型文件为空：{model}。请重新下载完整模型后重试；原始录音未被修改。"
            )
        vad_model = resolve_vad_model_path(self.runtime)
        return {
            "binary": binary,
            "model": str(model),
            "model_bytes": size,
            "vad_model": str(vad_model) if vad_model else None,
        }

    async def transcribe(self, path: str, mime_type: str | None) -> list[TranscriptSegment]:
        resolved = self.preflight()
        source = Path(path)
        if not source.is_file():
            raise LocalASRUnavailable(f"待转写的媒体分片不存在：{source}。请先重新准备分片。")
        scratch_root = self.work_dir
        if scratch_root:
            scratch_root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="kd-local-asr-", dir=str(scratch_root) if scratch_root else None) as scratch:
            workspace = Path(scratch)
            audio = await ensure_wav_16k(
                source, workspace, self.runtime.ffmpeg_path, self.runtime.timeout_seconds
            )
            prefix = workspace / "transcript"
            command = [
                resolved["binary"],
                "-m",
                resolved["model"],
                "-f",
                str(audio),
                "-oj",
                "-np",
                "-of",
                str(prefix),
            ]
            if self.runtime.language:
                command += ["-l", self.runtime.language]
            if self.runtime.threads > 0:
                command += ["-t", str(self.runtime.threads)]
            if self.runtime.initial_prompt:
                command += ["--prompt", self.runtime.initial_prompt]
            if resolved["vad_model"]:
                # 低阈值、长静音和较宽 speech padding 优先保留低音量发言；
                # 只跳过确定的静音，不改动 beam search / best-of 等识别质量参数。
                command += [
                    "--vad",
                    "--vad-model",
                    resolved["vad_model"],
                    "--vad-threshold",
                    "0.35",
                    "--vad-min-silence-duration-ms",
                    "1200",
                    "--vad-speech-pad-ms",
                    "400",
                    "--vad-samples-overlap",
                    "0.20",
                ]
            command += list(self.runtime.extra_args)
            await run_process(
                command,
                timeout=self.runtime.timeout_seconds,
                failure="whisper.cpp 本地转写失败",
            )
            return self._parse_report(prefix.with_suffix(".json"))

    @staticmethod
    def _parse_report(report: Path) -> list[TranscriptSegment]:
        if not report.is_file():
            raise ProviderOutputError(
                "whisper.cpp 没有生成 JSON 结果文件；请确认所用版本支持 -oj/--output-json。原始文件已保留。"
            )
        try:
            payload = json.loads(report.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise ProviderOutputError(f"whisper.cpp 的 JSON 结果无法解析：{exc}") from exc
        entries = payload.get("transcription")
        if entries is None:
            raise ProviderOutputError("whisper.cpp 的 JSON 结果缺少 transcription 字段，无法校验时间戳。")
        segments: list[TranscriptSegment] = []
        for entry in entries:
            text = (entry.get("text") or "").strip()
            if not text:
                continue
            offsets = entry.get("offsets") or {}
            start_ms, end_ms = offsets.get("from"), offsets.get("to")
            if start_ms is not None and end_ms is not None:
                start = float(start_ms) / 1000
                end = float(end_ms) / 1000
            else:
                timestamps = entry.get("timestamps") or {}
                parsed_start = clock_to_seconds(timestamps.get("from"))
                parsed_end = clock_to_seconds(timestamps.get("to"))
                if parsed_start is None or parsed_end is None:
                    raise ProviderOutputError(
                        "whisper.cpp 返回的分段缺少可用时间戳；系统不会伪造时间戳，请检查模型与参数。"
                    )
                start, end = parsed_start, parsed_end
            segments.append(TranscriptSegment(start_time=start, end_time=max(start, end), text=text))
        return segments


class LocalOpenAICompatibleASRProvider:
    """局域网内的 OpenAI 兼容转写服务（如 whisper.cpp server、faster-whisper-server）。"""

    requires_external_upload = False

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str | None = None,
        language: str = "zh",
        timeout_seconds: int = 1800,
        convert_to_wav: bool = False,
        ffmpeg_path: str = "ffmpeg",
        work_dir: Path | None = None,
        transcriptions_path: str = DEFAULT_TRANSCRIPTIONS_PATH,
    ):
        self.base_url = assert_local_endpoint(base_url)
        # whisper.cpp 自带的 whisper-server 用 /inference，不提供 OpenAI 路由，因此路径可配置。
        self.transcriptions_path = "/" + (transcriptions_path or DEFAULT_TRANSCRIPTIONS_PATH).strip("/")
        self.model = (model or "").strip()
        self.api_key = api_key
        self.language = language
        self.timeout_seconds = timeout_seconds
        # whisper.cpp server 只接受 WAV；faster-whisper 类服务可直接吃 FLAC，因此默认不转换。
        self.convert_to_wav = convert_to_wav
        self.ffmpeg_path = ffmpeg_path
        self.work_dir = work_dir

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def health(self) -> str:
        """先探 /models；whisper.cpp server 没有该接口，则回退探测服务根路径。"""

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                listing = await client.get(f"{self.base_url}/models", headers=self._headers())
                if listing.is_success:
                    return (
                        "本地 ASR 服务可达且未离开私网；实际转写质量仍取决于该服务加载的模型。"
                    )
                root = await client.get(f"{self.base_url}/", headers=self._headers())
        except httpx.HTTPError as exc:
            raise ProviderRequestError(
                "无法连接本地 ASR 服务。请确认服务已启动、端口正确且在同一私网内。"
            ) from exc
        if not root.is_success:
            raise ProviderRequestError(
                f"本地 ASR 服务不可用：/models 返回 HTTP {listing.status_code}，"
                f"根路径返回 HTTP {root.status_code}。请核对 Base URL 与端口。"
            )
        return (
            f"本地 ASR 服务可达（未实现 /models，whisper.cpp server 属于这种情况）；"
            f"转写路径为 {self.transcriptions_path}，请用真实音频验证一次。"
        )

    async def transcribe(self, path: str, mime_type: str | None) -> list[TranscriptSegment]:
        if not self.model:
            raise LocalASRUnavailable("本地 ASR 服务 Profile 缺少默认模型名，请填写该服务实际加载的模型 ID。")
        file_path = Path(path)
        if not file_path.is_file():
            raise LocalASRUnavailable(f"待转写的媒体分片不存在：{file_path}。请先重新准备分片。")
        scratch_root = self.work_dir
        if scratch_root:
            scratch_root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="kd-local-asr-", dir=str(scratch_root) if scratch_root else None) as scratch:
            upload = (
                await ensure_wav_16k(
                    file_path,
                    Path(scratch),
                    self.ffmpeg_path,
                    self.timeout_seconds,
                    force=True,
                )
                if self.convert_to_wav
                else file_path
            )
            payload = await self._post(upload, mime_type)
        return segments_from_verbose_json(payload)

    async def _post(self, upload: Path, mime_type: str | None) -> dict[str, Any]:
        data = {"model": self.model, "response_format": "verbose_json"}
        if self.language:
            data["language"] = self.language
        content_type = mimetypes.guess_type(upload.name)[0] or mime_type or "application/octet-stream"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                with upload.open("rb") as handle:
                    response = await client.post(
                        f"{self.base_url}{self.transcriptions_path}",
                        headers=self._headers(),
                        data=data,
                        files={"file": (upload.name, handle, content_type)},
                    )
        except httpx.HTTPError as exc:
            raise ProviderRequestError(
                "本地 ASR 服务请求没有完成。请检查服务、端口和私网连接；"
                "原始文件与已成功分片都已保留。"
            ) from exc
        if not response.is_success:
            raise ProviderRequestError(
                f"本地 ASR 服务返回 HTTP {response.status_code}。请检查该服务的模型、认证与运行状态；"
                "响应正文未写入任务记录。"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderOutputError("本地 ASR 服务返回的不是兼容 JSON，请检查服务类型和转写路径。") from exc
