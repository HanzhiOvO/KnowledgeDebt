#!/usr/bin/env python
"""本地 ASR 真机 smoke test：用真实音频量测速度与分段时间戳，不写数据库、不外发。

用法（在仓库根目录）：

    .venv/bin/python backend/scripts/local_asr_smoke.py 录音.aac --seconds 60

它会：
1. 读取 .env 中的 KNOWLEDGEDEBT_LOCAL_ASR_* 配置；
2. 需要时用 FFmpeg 截取前 N 秒（原文件不改动）；
3. 调用真实的 LocalWhisperCppProvider；
4. 打印实时倍速、分片时间戳与前几段文本，供选型参考。

判断标准：realtime factor 必须大于 1，否则转写会持续积压。
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.providers.local_asr import (  # noqa: E402
    LocalWhisperCppProvider,
    WhisperCppRuntime,
    resolve_executable,
    resolve_model_path,
)


def probe_duration(ffmpeg_path: str, source: Path) -> float | None:
    probe = str(Path(ffmpeg_path).with_name("ffprobe")) if "/" in ffmpeg_path else "ffprobe"
    if not resolve_executable(probe):
        return None
    result = subprocess.run(
        [probe, "-v", "error", "-show_entries", "format=duration", "-of",
         "default=noprint_wrappers=1:nokey=1", str(source)],
        check=False, capture_output=True, text=True, timeout=60,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def slice_audio(ffmpeg_path: str, source: Path, seconds: int, workspace: Path) -> Path:
    target = workspace / f"{source.stem}-first{seconds}s.wav"
    subprocess.run(
        [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y", "-t", str(seconds),
         "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target)],
        check=True, timeout=900,
    )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="本地 whisper.cpp 真机 smoke test")
    parser.add_argument("media", type=Path, help="真实音频或视频文件")
    parser.add_argument("--seconds", type=int, default=0, help="只测前 N 秒（0 表示整段）")
    parser.add_argument("--model", default="", help="覆盖模型（路径、文件名或 medium 简称）")
    parser.add_argument("--preview", type=int, default=5, help="打印前几段文本")
    args = parser.parse_args()

    if not args.media.is_file():
        print(f"找不到音频文件：{args.media}", file=sys.stderr)
        return 2

    settings = Settings.from_env()
    runtime = WhisperCppRuntime(
        binary_path=settings.local_asr_binary,
        model=args.model or settings.local_asr_model,
        model_dir=settings.local_asr_model_dir,
        language=settings.local_asr_language,
        threads=settings.local_asr_threads,
        timeout_seconds=settings.local_asr_timeout_seconds,
        initial_prompt=settings.local_asr_initial_prompt,
        ffmpeg_path=settings.ffmpeg_path,
    )
    model_path = resolve_model_path(runtime.model, runtime.model_dir)
    print("=== 本地 ASR 配置 ===")
    print(f"可执行文件 : {resolve_executable(runtime.binary_path) or runtime.binary_path + ' (未找到)'}")
    print(f"模型       : {model_path or (runtime.model or '未配置') + ' (未找到)'}")
    print(f"语言/线程  : {runtime.language} / {runtime.threads or '由 whisper.cpp 决定'}")

    with tempfile.TemporaryDirectory(prefix="kd-smoke-") as scratch:
        workspace = Path(scratch)
        target = args.media
        if args.seconds > 0:
            target = slice_audio(runtime.ffmpeg_path, args.media, args.seconds, workspace)
        audio_seconds = probe_duration(runtime.ffmpeg_path, target) or float(args.seconds or 0)
        provider = LocalWhisperCppProvider(runtime, work_dir=workspace)

        print(f"\n=== 开始转写：{target.name}（{audio_seconds:.1f} 秒）===")
        started = time.monotonic()
        try:
            segments = asyncio.run(provider.transcribe(str(target), None))
        except Exception as exc:  # noqa: BLE001 - smoke test 需要原样展示真实错误
            print(f"转写失败：{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        elapsed = time.monotonic() - started

    factor = (audio_seconds / elapsed) if elapsed > 0 and audio_seconds else 0.0
    print(f"耗时       : {elapsed:.1f} 秒")
    print(f"实时倍速   : {factor:.2f}×（需要 > 1 才能跟上课堂节奏）")
    print(f"分段数量   : {len(segments)}")
    for segment in segments[: args.preview]:
        print(f"  [{segment.start_time:7.2f} → {segment.end_time:7.2f}] {segment.text}")
    print(f"\n原始文件保持不变：{args.media}")
    return 0 if segments else 1


if __name__ == "__main__":
    raise SystemExit(main())
