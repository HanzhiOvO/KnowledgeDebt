"""本地 ASR 回归测试：真实子进程、真实 loopback HTTP、取消与超时、零外发边界。

所有测试只使用本机进程和 127.0.0.1，不访问外部网络，也不下载模型。
"""

import asyncio
import json
import os
import shutil
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from fastapi.testclient import TestClient

from app.automation import AutomationRepository
from app.config import Settings
from app.database import Database
from app.main import create_app
from app.models import CourseCreate, SessionCreate, TranscriptSegment
from app.provider_registry import PROVIDER_CATALOG, ProviderRegistry
from app.providers.base import ProviderNotConfigured, ProviderOutputError, ProviderRequestError
from app.providers.local_asr import (
    LOCAL_SERVICE_ADAPTER,
    WHISPER_CPP_ADAPTER,
    LocalASRTimeout,
    LocalOpenAICompatibleASRProvider,
    LocalWhisperCppProvider,
    WhisperCppRuntime,
    assert_local_endpoint,
    is_local_endpoint,
)
from app.secrets import SecretStore
from app.storage import LocalStorageProvider
from app.transcription import MediaPreparer, TranscriptionOrchestrator

WHISPER_STUB = '''#!/usr/bin/env python3
"""最小 whisper.cpp 替身：只实现本适配器真正依赖的命令行契约。"""
import json
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]


def flag(name: str) -> str | None:
    return args[args.index(name) + 1] if name in args else None


record = os.environ.get("KD_STUB_ARGV")
if record:
    Path(record).write_text("\\n".join(args), encoding="utf-8")
pidfile = os.environ.get("KD_STUB_PIDFILE")
if pidfile:
    Path(pidfile).write_text(str(os.getpid()), encoding="utf-8")

mode = os.environ.get("KD_STUB_MODE", "ok")
if mode == "fail":
    sys.stderr.write("whisper_init_from_file_with_params_no_state: failed to load model\\n")
    sys.exit(3)
if mode == "hang":
    time.sleep(300)
    sys.exit(0)
if mode == "noreport":
    sys.exit(0)

audio = Path(flag("-f") or "")
if not audio.is_file() or audio.stat().st_size == 0:
    sys.stderr.write("error: failed to open input file\\n")
    sys.exit(4)

entries = [
    {"timestamps": {"from": "00:00:00,000", "to": "00:00:02,500"}, "text": " 第一段中文内容"},
    {"timestamps": {"from": "00:00:02,500", "to": "00:00:05,000"}, "text": " 第二段中文内容"},
    {"timestamps": {"from": "00:00:05,000", "to": "00:00:06,000"}, "text": "   "},
]
if mode != "clock_only":
    for entry, (start, end) in zip(entries, [(0, 2500), (2500, 5000), (5000, 6000)], strict=True):
        entry["offsets"] = {"from": start, "to": end}

payload = {
    "systeminfo": "stub",
    "model": {"type": "stub", "path": flag("-m")},
    "params": {"language": flag("-l"), "threads": flag("-t")},
    "result": {"language": flag("-l") or "auto"},
    "transcription": entries,
}
prefix = flag("-of") or str(audio)
Path(prefix + ".json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
sys.exit(0)
'''

FFMPEG_STUB = '''#!/usr/bin/env python3
"""最小 FFmpeg 替身：只做复制，用于验证“非 WAV 分片会先转换”的代码路径。"""
import sys
from pathlib import Path

args = sys.argv[1:]
source = Path(args[args.index("-i") + 1])
target = Path(args[-1])
target.write_bytes(source.read_bytes())
sys.exit(0)
'''


def write_stub(path: Path, body: str) -> str:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def make_runtime(tmp_path: Path, **overrides) -> WhisperCppRuntime:
    binary = write_stub(tmp_path / "whisper-cli-stub", WHISPER_STUB)
    model = tmp_path / "ggml-small.bin"
    model.write_bytes(b"stub model weights")
    defaults = {
        "binary_path": binary,
        "model": str(model),
        "model_dir": tmp_path,
        "language": "zh",
        "threads": 4,
        "timeout_seconds": 60,
        "ffmpeg_path": "ffmpeg",
    }
    defaults.update(overrides)
    return WhisperCppRuntime(**defaults)


def wav_chunk(path: Path) -> Path:
    """写出一个最小合法 WAV 头，避免测试依赖真实音频素材。"""

    header = bytearray(44)
    header[0:4] = b"RIFF"
    header[4:8] = (36).to_bytes(4, "little")
    header[8:12] = b"WAVE"
    header[12:16] = b"fmt "
    header[16:20] = (16).to_bytes(4, "little")
    header[20:22] = (1).to_bytes(2, "little")
    header[22:24] = (1).to_bytes(2, "little")
    header[24:28] = (16000).to_bytes(4, "little")
    header[28:32] = (32000).to_bytes(4, "little")
    header[32:34] = (2).to_bytes(2, "little")
    header[34:36] = (16).to_bytes(2, "little")
    header[36:40] = b"data"
    path.write_bytes(bytes(header))
    return path


def test_local_endpoint_guard_accepts_private_hosts_and_refuses_public(monkeypatch):
    for allowed in (
        "http://127.0.0.1:8080/v1",
        "http://localhost:8080/v1",
        "http://192.168.1.20:9000/v1",
        "http://10.8.0.4:8080/v1",
        "http://100.101.102.103:8080/v1",  # Tailscale / CGNAT
        "http://dorm-server:8080/v1",
        "http://nas.local/v1",
    ):
        assert is_local_endpoint(allowed) is True
        assert assert_local_endpoint(allowed + "/") == allowed

    for refused in ("https://api.openai.com/v1", "http://8.8.8.8:8080/v1", "https://asr.example.com/v1"):
        assert is_local_endpoint(refused) is False
        with pytest.raises(ValueError, match="公网地址"):
            assert_local_endpoint(refused)
    with pytest.raises(ValueError, match="Base URL"):
        assert_local_endpoint("")


def test_whisper_cpp_parses_real_json_offsets_and_never_touches_source(tmp_path: Path, monkeypatch):
    argv_log = tmp_path / "argv.txt"
    monkeypatch.setenv("KD_STUB_ARGV", str(argv_log))
    monkeypatch.setenv("KD_STUB_MODE", "ok")
    source = wav_chunk(tmp_path / "chunk-0000.wav")
    original = source.read_bytes()
    scratch = tmp_path / "scratch"
    provider = LocalWhisperCppProvider(make_runtime(tmp_path), work_dir=scratch)

    segments = asyncio.run(provider.transcribe(str(source), "audio/wav"))

    assert provider.requires_external_upload is False
    assert [(item.start_time, item.end_time, item.text) for item in segments] == [
        (0.0, 2.5, "第一段中文内容"),
        (2.5, 5.0, "第二段中文内容"),
    ]
    invocation = argv_log.read_text(encoding="utf-8").split("\n")
    assert invocation[invocation.index("-l") + 1] == "zh"
    assert invocation[invocation.index("-t") + 1] == "4"
    assert "-oj" in invocation and "-of" in invocation
    assert invocation[invocation.index("-f") + 1] == str(source)
    assert source.read_bytes() == original
    # 临时工作目录用完即删，不会在数据目录里堆积中间文件。
    assert list(scratch.iterdir()) == []


def test_whisper_cpp_falls_back_to_clock_timestamps(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KD_STUB_MODE", "clock_only")
    source = wav_chunk(tmp_path / "chunk-0000.wav")
    provider = LocalWhisperCppProvider(make_runtime(tmp_path))

    segments = asyncio.run(provider.transcribe(str(source), None))

    assert [(item.start_time, item.end_time) for item in segments] == [(0.0, 2.5), (2.5, 5.0)]


def test_whisper_cpp_converts_non_wav_chunk_before_transcribing(tmp_path: Path, monkeypatch):
    argv_log = tmp_path / "argv.txt"
    monkeypatch.setenv("KD_STUB_ARGV", str(argv_log))
    monkeypatch.setenv("KD_STUB_MODE", "ok")
    ffmpeg = write_stub(tmp_path / "ffmpeg-stub", FFMPEG_STUB)
    source = tmp_path / "chunk-0000.flac"
    source.write_bytes(b"fake flac payload retained as-is")
    provider = LocalWhisperCppProvider(make_runtime(tmp_path, ffmpeg_path=ffmpeg))

    segments = asyncio.run(provider.transcribe(str(source), "audio/flac"))

    assert len(segments) == 2
    handed_to_whisper = argv_log.read_text(encoding="utf-8").split("\n")
    converted = handed_to_whisper[handed_to_whisper.index("-f") + 1]
    assert converted.endswith("chunk-0000-16k.wav")
    assert source.read_bytes() == b"fake flac payload retained as-is"


def test_whisper_cpp_without_ffmpeg_keeps_source_and_explains(tmp_path: Path):
    source = tmp_path / "chunk-0000.flac"
    source.write_bytes(b"retained flac")
    provider = LocalWhisperCppProvider(
        make_runtime(tmp_path, ffmpeg_path=str(tmp_path / "missing-ffmpeg"))
    )

    with pytest.raises(ProviderNotConfigured, match="需要 FFmpeg"):
        asyncio.run(provider.transcribe(str(source), "audio/flac"))
    assert source.read_bytes() == b"retained flac"


def test_whisper_cpp_missing_runtime_is_actionable(tmp_path: Path):
    source = wav_chunk(tmp_path / "chunk-0000.wav")
    missing_binary = LocalWhisperCppProvider(
        WhisperCppRuntime(binary_path=str(tmp_path / "not-installed"), model=str(source))
    )
    with pytest.raises(ProviderNotConfigured, match="未找到 whisper.cpp 可执行文件"):
        asyncio.run(missing_binary.transcribe(str(source), None))

    missing_model = LocalWhisperCppProvider(
        make_runtime(tmp_path, model="ggml-medium.bin", model_dir=tmp_path / "empty")
    )
    with pytest.raises(ProviderNotConfigured, match="未找到 whisper.cpp 模型"):
        asyncio.run(missing_model.transcribe(str(source), None))
    assert source.is_file()


def test_whisper_cpp_failure_surfaces_real_stderr(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KD_STUB_MODE", "fail")
    source = wav_chunk(tmp_path / "chunk-0000.wav")
    provider = LocalWhisperCppProvider(make_runtime(tmp_path))

    with pytest.raises(ProviderRequestError, match="failed to load model"):
        asyncio.run(provider.transcribe(str(source), None))
    assert source.is_file()


def test_whisper_cpp_missing_json_report_is_not_silently_empty(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KD_STUB_MODE", "noreport")
    source = wav_chunk(tmp_path / "chunk-0000.wav")
    provider = LocalWhisperCppProvider(make_runtime(tmp_path))

    with pytest.raises(ProviderOutputError, match="JSON 结果文件"):
        asyncio.run(provider.transcribe(str(source), None))


def assert_process_gone(pidfile: Path, timeout: float = 5.0) -> int:
    deadline = time.monotonic() + timeout
    pid = int(pidfile.read_text(encoding="utf-8").strip())
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return pid
        time.sleep(0.05)
    raise AssertionError(f"本地转写进程 {pid} 在超时/取消后仍在运行")


def test_whisper_cpp_timeout_terminates_local_process(tmp_path: Path, monkeypatch):
    pidfile = tmp_path / "stub.pid"
    monkeypatch.setenv("KD_STUB_MODE", "hang")
    monkeypatch.setenv("KD_STUB_PIDFILE", str(pidfile))
    source = wav_chunk(tmp_path / "chunk-0000.wav")
    provider = LocalWhisperCppProvider(make_runtime(tmp_path, timeout_seconds=1))

    with pytest.raises(LocalASRTimeout, match="已终止本地进程"):
        asyncio.run(provider.transcribe(str(source), None))

    assert_process_gone(pidfile)
    assert source.is_file()


def test_whisper_cpp_cancellation_kills_local_process(tmp_path: Path, monkeypatch):
    pidfile = tmp_path / "stub.pid"
    monkeypatch.setenv("KD_STUB_MODE", "hang")
    monkeypatch.setenv("KD_STUB_PIDFILE", str(pidfile))
    source = wav_chunk(tmp_path / "chunk-0000.wav")
    provider = LocalWhisperCppProvider(make_runtime(tmp_path, timeout_seconds=300))

    async def scenario() -> None:
        task = asyncio.ensure_future(provider.transcribe(str(source), None))
        for _ in range(200):
            if pidfile.exists():
                break
            await asyncio.sleep(0.05)
        assert pidfile.exists(), "替身进程没有启动"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.2)

    asyncio.run(scenario())
    assert_process_gone(pidfile)


class _VerboseJSONHandler(BaseHTTPRequestHandler):
    received: dict = {}

    def do_GET(self):  # noqa: N802 - http.server 约定
        if self.path.endswith("/models"):
            self._json({"data": [{"id": "whisper-small"}]})
        else:
            self.send_error(404)

    def do_POST(self):  # noqa: N802 - http.server 约定
        if not (self.path.endswith("/audio/transcriptions") or self.path.endswith("/inference")):
            self.send_error(404)
            return
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        type(self).received = {"path": self.path, "body": body}
        self._json(
            {
                "text": "整段兜底文本",
                "duration": 6.0,
                "segments": [
                    {"start": 0.0, "end": 2.5, "text": " 本地服务第一段"},
                    {"start": 2.5, "end": 5.0, "text": " 本地服务第二段"},
                    {"start": 5.0, "end": 6.0, "text": "  "},
                ],
            }
        )

    def _json(self, payload: dict) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args):  # 保持测试输出干净
        return


class _WhisperServerHandler(_VerboseJSONHandler):
    """模拟 whisper.cpp 自带 server：没有 /models，只有 / 与 /inference。"""

    received: dict = {}

    def do_GET(self):  # noqa: N802 - http.server 约定
        if self.path == "/":
            body = b"whisper.cpp server"
            self.send_response(200)
            self.send_header("content-type", "text/plain")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)


def test_local_service_provider_talks_to_loopback_and_parses_segments(tmp_path: Path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _VerboseJSONHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}/v1"
        provider = LocalOpenAICompatibleASRProvider(base_url, "whisper-small", language="zh")
        chunk = wav_chunk(tmp_path / "chunk-0000.wav")

        assert asyncio.run(provider.health()).startswith("本地 ASR 服务可达")
        segments = asyncio.run(provider.transcribe(str(chunk), "audio/wav"))
    finally:
        server.shutdown()
        server.server_close()

    assert provider.requires_external_upload is False
    assert [(item.start_time, item.end_time, item.text) for item in segments] == [
        (0.0, 2.5, "本地服务第一段"),
        (2.5, 5.0, "本地服务第二段"),
    ]
    request = _VerboseJSONHandler.received
    assert request["path"].endswith("/v1/audio/transcriptions")
    assert b'name="model"' in request["body"] and b"whisper-small" in request["body"]
    assert b"verbose_json" in request["body"]


def test_openai_compatible_asr_uploads_multipart_over_loopback(tmp_path: Path):
    """守护共享的 multipart 上传路径：async with 不能包裹同步文件句柄。"""

    from app.providers.openai_compatible import OpenAICompatibleProvider

    server = ThreadingHTTPServer(("127.0.0.1", 0), _VerboseJSONHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        provider = OpenAICompatibleProvider(
            "test-key-not-a-real-secret",
            f"http://127.0.0.1:{server.server_port}/v1",
            "test-ai",
            "test-asr",
        )
        chunk = wav_chunk(tmp_path / "chunk-0000.wav")
        segments = asyncio.run(provider.transcribe(str(chunk), "audio/wav"))
    finally:
        server.shutdown()
        server.server_close()

    assert [item.text for item in segments] == ["本地服务第一段", "本地服务第二段"]


def test_local_service_provider_can_convert_to_wav_for_wav_only_servers(tmp_path: Path):
    ffmpeg = write_stub(tmp_path / "ffmpeg-stub", FFMPEG_STUB)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _VerboseJSONHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        provider = LocalOpenAICompatibleASRProvider(
            f"http://127.0.0.1:{server.server_port}/v1",
            "whisper-small",
            convert_to_wav=True,
            ffmpeg_path=ffmpeg,
            work_dir=tmp_path / "scratch",
        )
        chunk = tmp_path / "chunk-0000.flac"
        chunk.write_bytes(b"retained flac chunk")
        segments = asyncio.run(provider.transcribe(str(chunk), "audio/flac"))
    finally:
        server.shutdown()
        server.server_close()

    assert len(segments) == 2
    assert b"chunk-0000-16k.wav" in _VerboseJSONHandler.received["body"]
    assert chunk.read_bytes() == b"retained flac chunk"
    assert list((tmp_path / "scratch").iterdir()) == []


def test_local_service_provider_supports_whisper_cpp_inference_path(tmp_path: Path):
    """whisper.cpp 自带 server 只有 /inference，也没有 /models：两者都必须被支持。"""

    server = ThreadingHTTPServer(("127.0.0.1", 0), _WhisperServerHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        provider = LocalOpenAICompatibleASRProvider(
            base_url, "ggml-medium", transcriptions_path="inference"
        )
        default_path = LocalOpenAICompatibleASRProvider(base_url, "ggml-medium")
        chunk = wav_chunk(tmp_path / "chunk-0000.wav")
        health = asyncio.run(provider.health())
        segments = asyncio.run(provider.transcribe(str(chunk), "audio/wav"))
    finally:
        server.shutdown()
        server.server_close()

    assert provider.transcriptions_path == "/inference"
    assert default_path.transcriptions_path == "/audio/transcriptions"
    assert "未实现 /models" in health and "/inference" in health
    assert _WhisperServerHandler.received["path"].endswith("/inference")
    assert len(segments) == 2


def test_local_service_provider_refuses_public_base_url():
    with pytest.raises(ValueError, match="公网地址"):
        LocalOpenAICompatibleASRProvider("https://api.openai.com/v1", "whisper-1")


def local_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "data_dir": tmp_path,
        "ai_provider": "test",
        "asr_provider": "test",
        "api_key": None,
        "base_url": "http://invalid",
        "ai_model": "test",
        "asr_model": "test",
    }
    values.update(overrides)
    return Settings(**values)


def test_registry_builds_local_whisper_profile_and_reports_readiness(tmp_path: Path, monkeypatch):
    runtime = make_runtime(tmp_path)
    settings = local_settings(
        tmp_path,
        local_asr_binary=runtime.binary_path,
        local_asr_model=runtime.model,
        local_asr_model_dir=tmp_path,
        local_asr_timeout_seconds=60,
    )
    automation = AutomationRepository(Database(tmp_path / "registry.sqlite3"))
    registry = ProviderRegistry(automation, SecretStore(None), settings)

    status = registry.local_asr_status()
    assert status["ready"] is True
    assert status["binary_ready"] and status["model_ready"]
    assert status["language"] == "zh"

    registry.ensure_environment_profiles()
    defaults = automation.get_provider_defaults()
    # whisper.cpp 就绪时默认走本地转写：不外发、无需逐次授权。
    assert defaults["asr"]["adapter"] == WHISPER_CPP_ADAPTER
    assert defaults["asr"]["external"] is False

    provider = registry.build(defaults["asr"]["id"])
    assert isinstance(provider, LocalWhisperCppProvider)
    monkeypatch.setenv("KD_STUB_MODE", "ok")
    tested = asyncio.run(registry.test_connection(defaults["asr"]["id"]))
    assert tested["last_test_status"] == "succeeded"
    assert "已就绪" in tested["last_test_message"]


def test_registry_keeps_external_default_when_local_runtime_is_missing(tmp_path: Path):
    settings = local_settings(tmp_path, local_asr_binary=str(tmp_path / "absent"), local_asr_model="")
    automation = AutomationRepository(Database(tmp_path / "registry-missing.sqlite3"))
    registry = ProviderRegistry(automation, SecretStore(None), settings)

    assert registry.local_asr_status()["ready"] is False
    registry.ensure_environment_profiles()
    defaults = automation.get_provider_defaults()
    assert defaults["asr"]["adapter"] == "openai_compatible"

    local_profile = next(
        profile
        for profile in automation.list_provider_profiles()
        if profile["adapter"] == WHISPER_CPP_ADAPTER
    )
    failed = asyncio.run(registry.test_connection(local_profile["id"]))
    assert failed["last_test_status"] == "failed"
    assert "whisper.cpp" in failed["last_test_message"]


def test_whisper_cpp_catalog_entry_does_not_claim_long_audio_or_diarization():
    entry = next(item for item in PROVIDER_CATALOG if item["adapter"] == WHISPER_CPP_ADAPTER)
    assert entry["capabilities"] == ["audio_transcription", "segment_timestamps"]
    assert "long_audio" not in entry["capabilities"]
    assert "speaker_diarization" not in entry["capabilities"]
    assert entry["implementation_status"] == "local_runtime_required"


def build_local_session(tmp_path: Path) -> tuple[Database, AutomationRepository, dict, Path]:
    database = Database(tmp_path / "local-asr.sqlite3")
    automation = AutomationRepository(database)
    course = database.create_course(CourseCreate(name="操作系统", semester="2026 秋"))
    session = database.create_session(course["id"], SessionCreate(title="本地转写课堂"))
    media = wav_chunk(tmp_path / "lecture.wav")
    resource = database.add_resource(
        session["id"],
        type="audio",
        evidence_level="classroom",
        name="lecture.wav",
        mime_type="audio/wav",
        local_path=str(media),
        duration_seconds=6,
    )
    automation.ensure_resource_automation(resource["id"])
    return database, automation, resource, media


def test_local_asr_end_to_end_needs_no_consent_and_stays_offline(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KD_STUB_MODE", "ok")
    database, automation, resource, media = build_local_session(tmp_path)
    runtime = make_runtime(tmp_path)
    provider = LocalWhisperCppProvider(runtime, work_dir=tmp_path / "scratch")
    profile = {
        "id": "local-whisper",
        "name": "本地 whisper.cpp",
        "default_model": Path(runtime.model).name,
        "external": False,
        "capabilities": ["audio_transcription", "segment_timestamps"],
    }
    orchestrator = TranscriptionOrchestrator(
        database,
        automation,
        LocalStorageProvider(tmp_path / "objects"),
        lambda: (profile, provider),
        MediaPreparer(str(tmp_path / "no-ffmpeg-needed"), tmp_path / "chunks", 1500),
        lambda session_id: None,
    )

    job, created = orchestrator.create_job(resource["id"], confirmed_external_upload=False)
    assert created is True
    result = asyncio.run(orchestrator.run(job["id"]))

    assert result["status"] == "succeeded"
    assert automation.get_resource_automation(resource["id"])["transcription_state"] == "transcribed"
    segments = database.list_transcript_segments(resource["id"])
    assert [(item["global_start"], item["global_end"]) for item in segments] == [(0.0, 2.5), (2.5, 5.0)]
    assert media.is_file()
    usage = automation.provider_usage()
    assert usage["items"][0]["status"] == "succeeded"


class GatedProvider:
    """第一个分片立刻完成，第二个分片等待事件，用于验证运行中取消。"""

    requires_external_upload = False

    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = False
        self.calls: list[str] = []

    async def transcribe(self, path: str, mime_type: str | None) -> list[TranscriptSegment]:
        name = Path(path).name
        self.calls.append(name)
        if name == "chunk-0001.flac":
            self.started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return [TranscriptSegment(start_time=1, end_time=3, text=f"segment from {name}")]


class TwoChunkPreparer:
    def __init__(self, root: Path):
        self.root = root

    def prepare(self, resource_id: str, source: Path, duration_seconds: float | None) -> list[dict]:
        chunks = []
        for position in range(2):
            path = self.root / f"chunk-{position:04d}.flac"
            path.write_bytes(b"retained chunk audio")
            chunks.append(
                {
                    "start_seconds": position * 60.0,
                    "end_seconds": (position + 1) * 60.0,
                    "media_path": str(path),
                }
            )
        return chunks


def test_cancel_during_running_chunk_keeps_successful_chunks_and_resumes(tmp_path: Path):
    database, automation, resource, media = build_local_session(tmp_path)
    provider = GatedProvider()
    profile = {
        "id": "local-gated",
        "name": "本地测试 ASR",
        "default_model": "stub",
        "external": False,
        "capabilities": [],
    }
    orchestrator = TranscriptionOrchestrator(
        database,
        automation,
        LocalStorageProvider(tmp_path / "objects"),
        lambda: (profile, provider),
        TwoChunkPreparer(tmp_path),
        lambda session_id: None,
        cancel_poll_seconds=0.05,
    )
    job, _ = orchestrator.create_job(resource["id"], confirmed_external_upload=False)

    async def scenario() -> dict:
        run = asyncio.ensure_future(orchestrator.run(job["id"]))
        await asyncio.wait_for(provider.started.wait(), timeout=5)
        database.cancel_job(job["id"])
        return await asyncio.wait_for(run, timeout=5)

    cancelled = asyncio.run(scenario())

    assert cancelled["status"] == "cancelled"
    assert provider.cancelled is True
    assert automation.get_resource_automation(resource["id"])["transcription_state"] == "cancelled"
    chunk_states = {
        chunk["position"]: chunk["status"]
        for chunk in automation.list_transcription_chunks(resource["id"])
    }
    assert chunk_states == {0: "succeeded", 1: "pending"}
    assert database.list_transcript_segments(resource["id"])[0]["global_start"] == 1
    assert media.is_file()

    retry_job, retry_created = orchestrator.create_job(resource["id"], confirmed_external_upload=False)
    assert retry_created is True
    provider.started.clear()

    async def resume() -> dict:
        return await asyncio.wait_for(orchestrator.run(retry_job["id"]), timeout=10)

    # 续跑时第二个分片不再等待，直接完成。
    provider.calls.clear()
    original_transcribe = provider.transcribe

    async def fast_transcribe(path: str, mime_type: str | None):
        if Path(path).name == "chunk-0001.flac":
            provider.calls.append("chunk-0001.flac")
            return [TranscriptSegment(start_time=1, end_time=3, text="resumed chunk")]
        return await original_transcribe(path, mime_type)

    provider.transcribe = fast_transcribe  # type: ignore[method-assign]
    finished = asyncio.run(resume())

    assert finished["status"] == "succeeded"
    assert provider.calls == ["chunk-0001.flac"]
    segments = database.list_transcript_segments(resource["id"])
    assert [(item["global_start"], item["global_end"]) for item in segments] == [(1, 3), (61, 63)]


def test_api_rejects_public_base_url_and_forces_local_flag(tmp_path: Path):
    client = TestClient(
        create_app(
            local_settings(tmp_path, local_asr_binary=str(tmp_path / "absent")),
            Database(tmp_path / "api-local-asr.sqlite3"),
        )
    )

    public = client.post(
        "/settings/providers",
        json={
            "name": "伪装成本地的公网 ASR",
            "vendor": "local_asr_service",
            "adapter": LOCAL_SERVICE_ADAPTER,
            "base_url": "https://api.openai.com/v1",
            "default_model": "whisper-1",
            "capabilities": ["audio_transcription"],
            "external": False,
        },
    )
    assert public.status_code == 422
    assert "公网地址" in public.json()["detail"]

    created = client.post(
        "/settings/providers",
        json={
            "name": "寝室服务器 ASR",
            "vendor": "local_asr_service",
            "adapter": LOCAL_SERVICE_ADAPTER,
            "base_url": "http://192.168.1.30:8080/v1/",
            "default_model": "whisper-small",
            "capabilities": ["audio_transcription", "segment_timestamps"],
            "external": True,  # 客户端谎报为外部也会被强制纠正为本地
        },
    )
    assert created.status_code == 201
    profile = created.json()
    assert profile["external"] is False
    assert profile["base_url"] == "http://192.168.1.30:8080/v1"
    assert profile["implementation_status"] == "local_runtime_required"

    routed = client.put("/settings/providers/defaults/asr", json={"profile_id": profile["id"]})
    assert routed.status_code == 200

    settings_payload = client.get("/settings/provider").json()
    assert settings_payload["defaults"]["asr"]["id"] == profile["id"]
    assert settings_payload["local_asr"]["ready"] is False
    assert settings_payload["local_asr"]["adapter"] == WHISPER_CPP_ADAPTER

    rejected_update = client.patch(
        f"/settings/providers/{profile['id']}", json={"base_url": "https://asr.example.com/v1"}
    )
    assert rejected_update.status_code == 422


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要真实 FFmpeg 才能验证真实音频转换")
def test_real_ffmpeg_produces_whisper_ready_wav(tmp_path: Path, monkeypatch):
    """用真实 FFmpeg 生成音频并验证转换链路，不依赖任何外部网络。"""

    monkeypatch.setenv("KD_STUB_MODE", "ok")
    source = tmp_path / "tone.flac"
    generate = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=2",
        "-ac",
        "2",
        "-ar",
        "44100",
        str(source),
    ]
    assert os.system(" ".join(f'"{part}"' for part in generate)) == 0
    argv_log = tmp_path / "argv.txt"
    monkeypatch.setenv("KD_STUB_ARGV", str(argv_log))
    provider = LocalWhisperCppProvider(make_runtime(tmp_path), work_dir=tmp_path / "scratch")

    segments = asyncio.run(provider.transcribe(str(source), "audio/flac"))

    assert len(segments) == 2
    handed = argv_log.read_text(encoding="utf-8").split("\n")
    assert handed[handed.index("-f") + 1].endswith("-16k.wav")
    assert source.is_file()
