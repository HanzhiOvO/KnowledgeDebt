from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class RecordingMediaError(RuntimeError):
    """Raw recording chunks could not be validated or safely packaged."""


@dataclass(frozen=True)
class AssembledRecording:
    path: Path
    duration_seconds: float
    mime_type: str = "audio/flac"
    extension: str = ".flac"


class RecordingAssembler(Protocol):
    def assemble(self, recording_id: str, chunks: list[Any]) -> AssembledRecording: ...

    def verify(self, path: Path) -> float: ...


class BrowserRecordingAssembler:
    """Turn one or more MediaRecorder streams into one verified speech recording.

    Timeslices from one MediaRecorder instance are fragments of the same container and
    must first be joined byte-for-byte. Resuming after a refresh creates a new container,
    so those containers are decoded separately and concatenated by FFmpeg.
    """

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str | None = None,
        *,
        timeout_seconds: int = 7200,
    ):
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.timeout_seconds = timeout_seconds

    def assemble(self, recording_id: str, chunks: list[Any]) -> AssembledRecording:
        if not chunks:
            raise RecordingMediaError("没有可封装的录音分片；原始录音没有被删除。")
        ffmpeg = self._resolve_binary(self.ffmpeg_path, "FFmpeg")
        groups = self._group_chunks(chunks)
        chunk_directory = Path(chunks[0]["local_path"]).resolve().parent
        work_directory = chunk_directory / f".assemble-{uuid.uuid4().hex}"
        output = chunk_directory / f".{recording_id}-{uuid.uuid4().hex}.flac"
        work_directory.mkdir(parents=True, exist_ok=False)
        streams: list[Path] = []
        try:
            for stream_index, stream_chunks in groups:
                segment = work_directory / f"stream-{stream_index:05d}.media"
                self._join_stream(segment, chunk_directory, stream_chunks)
                self._probe(segment, require_duration=False)
                streams.append(segment)
            command = [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
            ]
            for stream in streams:
                command.extend(("-i", str(stream)))
            if len(streams) == 1:
                command.extend(("-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000"))
            else:
                filters = [
                    (
                        f"[{index}:a:0]aformat=sample_fmts=s16:sample_rates=16000:"
                        f"channel_layouts=mono[a{index}]"
                    )
                    for index in range(len(streams))
                ]
                inputs = "".join(f"[a{index}]" for index in range(len(streams)))
                filters.append(f"{inputs}concat=n={len(streams)}:v=0:a=1[out]")
                command.extend(("-filter_complex", ";".join(filters), "-map", "[out]"))
            command.extend(("-map_metadata", "-1", "-c:a", "flac", str(output)))
            self._run(command, "无法安全封装浏览器录音")
            duration = self.verify(output)
            with output.open("rb+") as handle:
                os.fsync(handle.fileno())
            return AssembledRecording(path=output, duration_seconds=duration)
        except Exception:
            output.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(work_directory, ignore_errors=True)

    def verify(self, path: Path) -> float:
        return self._probe(path, require_duration=True)

    def _probe(self, path: Path, *, require_duration: bool) -> float:
        ffprobe = self._resolve_ffprobe()
        completed = self._run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,duration",
                "-of",
                "json",
                str(path),
            ],
            "录音媒体校验失败",
        )
        try:
            payload = json.loads(completed.stdout or "{}")
            streams = payload.get("streams") or []
            if not any(item.get("codec_type") == "audio" for item in streams):
                raise ValueError("媒体文件中没有可读取的音轨，原始文件已保留。")
            raw_duration = (payload.get("format") or {}).get("duration")
            if raw_duration in {None, "N/A"}:
                raw_duration = next(
                    (
                        item.get("duration")
                        for item in streams
                        if item.get("codec_type") == "audio"
                        and item.get("duration") not in {None, "N/A"}
                    ),
                    None,
                )
            duration = float(raw_duration) if raw_duration is not None else 0.0
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            if not require_duration and "no audio stream" not in str(exc):
                # Some live WebM fragments do not publish duration, but must contain audio.
                try:
                    if any(item.get("codec_type") == "audio" for item in payload.get("streams", [])):
                        return 0.0
                except (AttributeError, UnboundLocalError):
                    pass
            raise RecordingMediaError("录音分片不是可读取的音频；原始分片已保留。") from exc
        if require_duration and duration <= 0:
            raise RecordingMediaError("最终录音缺少有效时长；原始分片已保留。")
        return duration

    @staticmethod
    def _group_chunks(chunks: list[Any]) -> list[tuple[int, list[Any]]]:
        grouped: dict[int, list[Any]] = defaultdict(list)
        stream_ids: dict[int, str] = {}
        previous_index = -1
        for chunk in sorted(chunks, key=lambda item: int(item["sequence"])):
            stream_index = int(chunk.get("stream_index", 0))
            stream_id = str(chunk.get("stream_id") or "legacy")
            if stream_index < previous_index:
                raise RecordingMediaError("录音流顺序不一致；请保留分片并重新尝试保存。")
            previous_index = stream_index
            known_id = stream_ids.setdefault(stream_index, stream_id)
            if known_id != stream_id:
                raise RecordingMediaError("同一录音流包含冲突标识；原始分片已保留。")
            grouped[stream_index].append(chunk)
        indexes = sorted(grouped)
        if indexes != list(range(indexes[0], indexes[-1] + 1)):
            raise RecordingMediaError("录音流编号不连续；原始分片已保留。")
        return [(index, grouped[index]) for index in indexes]

    @staticmethod
    def _join_stream(target: Path, chunk_directory: Path, chunks: list[Any]) -> None:
        with target.open("xb") as output:
            for chunk in chunks:
                source = Path(chunk["local_path"]).resolve()
                if source.parent != chunk_directory:
                    raise RecordingMediaError("录音分片路径越界；已停止封装。")
                if not source.is_file() or source.stat().st_size != int(chunk["byte_size"]):
                    raise RecordingMediaError(
                        f"录音分片 {chunk['sequence']} 丢失或大小不匹配；原始分片未清理。"
                    )
                digest = hashlib.sha256()
                with source.open("rb") as handle:
                    while block := handle.read(1024 * 1024):
                        digest.update(block)
                        output.write(block)
                if digest.hexdigest() != chunk["checksum"]:
                    raise RecordingMediaError(
                        f"录音分片 {chunk['sequence']} 校验失败；请重新上传该分片。"
                    )
            output.flush()
            os.fsync(output.fileno())

    def _resolve_ffprobe(self) -> str:
        if self.ffprobe_path:
            return self._resolve_binary(self.ffprobe_path, "FFprobe")
        ffmpeg = Path(self.ffmpeg_path).expanduser()
        if ffmpeg.parent != Path("."):
            sibling = ffmpeg.with_name("ffprobe")
            if sibling.is_file() and os.access(sibling, os.X_OK):
                return str(sibling.resolve())
        return self._resolve_binary("ffprobe", "FFprobe")

    @staticmethod
    def _resolve_binary(configured: str, label: str) -> str:
        candidate = Path(configured).expanduser()
        if candidate.parent != Path("."):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate.resolve())
        else:
            resolved = shutil.which(configured)
            if resolved:
                return resolved
        raise RecordingMediaError(
            f"应用内置 {label} 不可用，暂时不能安全完成录音；所有原始分片均已保留。"
        )

    def _run(self, command: list[str], message: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RecordingMediaError(f"{message}：处理超时；原始分片已保留。") from exc
        except subprocess.CalledProcessError as exc:
            detail = " ".join((exc.stderr or "").strip().split())[-400:]
            suffix = f"（{detail}）" if detail else ""
            raise RecordingMediaError(f"{message}{suffix}；原始分片已保留。") from exc
