from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .automation import AutomationRepository, parse_iso
from .database import Database
from .models import JobStatus
from .providers.base import TranscriptionProvider
from .storage import StorageProvider, StoredObject
from .timezones import local_date


class FFmpegUnavailable(RuntimeError):
    pass


class MediaInspectionFailed(RuntimeError):
    """FFprobe 存在但无法证明媒体可读或取得可靠时长。"""


class TranscriptionCancelled(RuntimeError):
    """用户在分片转写过程中取消了 Job；已终止本地进程并保留成功分片。"""


class MediaPreparer:
    DIRECT_SUFFIXES = {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm", ".flac", ".ogg"}

    def __init__(self, ffmpeg_path: str, output_root: Path, chunk_seconds: int):
        self.ffmpeg_path = ffmpeg_path
        self.output_root = output_root
        self.chunk_seconds = chunk_seconds

    def prepare(self, resource_id: str, source: Path, duration_seconds: float | None) -> list[dict[str, Any]]:
        if not source.is_file() or source.stat().st_size == 0:
            raise MediaInspectionFailed("待转写媒体不存在或为空；原始资源记录已保留，请重新选择有效文件。")
        duration = duration_seconds if duration_seconds and duration_seconds > 0 else None
        if duration is None:
            duration = self._probe_duration(source, required=True)
        direct = source.suffix.lower() in self.DIRECT_SUFFIXES
        if duration <= self.chunk_seconds and direct:
            return [{"start_seconds": 0.0, "end_seconds": duration, "media_path": str(source)}]
        self._require_ffmpeg()
        target_dir = self.output_root / resource_id
        target_dir.mkdir(parents=True, exist_ok=True)
        chunks: list[dict[str, Any]] = []
        position = 0
        start = 0.0
        while start < duration:
            end = min(duration, start + self.chunk_seconds)
            target = target_dir / f"chunk-{position:04d}.flac"
            if not target.exists() or target.stat().st_size == 0:
                self._convert(source, target, start, end - start)
            chunks.append(
                {"start_seconds": start, "end_seconds": end, "media_path": str(target)}
            )
            position += 1
            start = end
        return chunks

    def _require_ffmpeg(self) -> None:
        if not shutil.which(self.ffmpeg_path) and not Path(self.ffmpeg_path).exists():
            raise FFmpegUnavailable(
                "需要 FFmpeg 才能规范化或切分该媒体，但当前未找到可执行文件。"
                "请安装 FFmpeg，或设置 KNOWLEDGEDEBT_FFMPEG_PATH。原始文件已安全保留，可配置后重试。"
            )

    def _probe_duration(self, source: Path, *, required: bool = False) -> float | None:
        probe = str(Path(self.ffmpeg_path).with_name("ffprobe")) if "/" in self.ffmpeg_path else "ffprobe"
        if not shutil.which(probe) and not Path(probe).exists():
            if required:
                raise FFmpegUnavailable(
                    "需要 FFprobe 检查媒体是否可读并确定时长，但当前未找到可执行文件。"
                    "请安装 FFmpeg/FFprobe，或设置 KNOWLEDGEDEBT_FFMPEG_PATH 指向同目录的 FFmpeg。"
                    "系统不会让未知时长媒体绕过分片限制；原始文件已安全保留。"
                )
            return None
        result = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(source),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            if required:
                diagnostic = (result.stderr or "unknown FFprobe error").strip()[-600:]
                raise MediaInspectionFailed(
                    f"FFprobe 无法读取该媒体或确定时长：{diagnostic}。"
                    "系统没有创建转写请求，原始文件已安全保留。"
                )
            return None
        try:
            value = float(result.stdout.strip())
        except ValueError:
            if required:
                raise MediaInspectionFailed(
                    "FFprobe 没有返回有效媒体时长；系统不会让未知时长媒体绕过分片限制。"
                    "原始文件已安全保留。"
                ) from None
            return None
        if value <= 0:
            if required:
                raise MediaInspectionFailed(
                    "FFprobe 返回的媒体时长无效；请检查文件是否包含可解码的音频轨。"
                    "原始文件已安全保留。"
                )
            return None
        return value

    def _convert(self, source: Path, target: Path, start: float, duration: float | None) -> None:
        command = [self.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y"]
        if start:
            command += ["-ss", f"{start:.3f}"]
        command += ["-i", str(source)]
        if duration is not None:
            command += ["-t", f"{duration:.3f}"]
        command += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "flac", str(target)]
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=900)
        if result.returncode != 0:
            target.unlink(missing_ok=True)
            diagnostic = (result.stderr or "unknown FFmpeg error").strip()[-600:]
            raise RuntimeError(f"FFmpeg 媒体处理失败：{diagnostic}")


class TranscriptionOrchestrator:
    def __init__(
        self,
        db: Database,
        automation: AutomationRepository,
        storage: StorageProvider,
        provider_resolver: Callable[[], tuple[dict[str, Any], TranscriptionProvider]],
        media_preparer: MediaPreparer,
        score_refresher: Callable[[str], Any],
        cancel_poll_seconds: float = 1.0,
    ):
        self.db = db
        self.automation = automation
        self.storage = storage
        self.provider_resolver = provider_resolver
        self.media_preparer = media_preparer
        self.score_refresher = score_refresher
        self.cancel_poll_seconds = max(0.02, cancel_poll_seconds)
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    def create_job(
        self,
        resource_id: str,
        *,
        confirmed_external_upload: bool,
        profile: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        resource = self.db.get_resource(resource_id)
        if resource["type"] not in {"audio", "video"}:
            raise ValueError("只有录音或视频资源可以进入转写队列。")
        existing = self.automation.active_transcription_job(resource_id)
        if existing:
            return existing, False
        if profile is None:
            profile, _ = self.provider_resolver()
        if profile.get("external") and not confirmed_external_upload:
            self.automation.update_resource_transcription(resource_id, "awaiting_consent")
            raise PermissionError("external transcription requires explicit one-time consent")
        job = self.db.create_job(
            "transcription",
            session_id=resource["session_id"],
            resource_id=resource_id,
            payload={
                "confirmed_external_upload": confirmed_external_upload,
                "provider_profile_id": profile.get("id"),
                "provider": profile.get("name", "injected provider"),
                "model": profile.get("default_model"),
            },
        )
        self.automation.update_resource_transcription(resource_id, "queued", job_id=job["id"])
        return job, True

    def start(self, job_id: str) -> None:
        running = self._tasks.get(job_id)
        if running and not running.done():
            return
        self._tasks[job_id] = asyncio.create_task(self.run(job_id))

    async def adopt_unfinished(self) -> None:
        for job in self.db.list_jobs():
            if job["kind"] == "transcription" and job["status"] in {"queued", "running"}:
                if job["status"] == "running":
                    self.db.update_job(job["id"], status="queued", stage="resuming_after_restart")
                self.start(job["id"])

    async def shutdown(self) -> None:
        pending = [task for task in self._tasks.values() if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def run(self, job_id: str) -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if job["status"] == JobStatus.CANCELLED.value:
            return job
        resource_id = job["resource_id"]
        resource = self.db.get_resource(resource_id)
        started = time.monotonic()
        request_count = 0
        profile: dict[str, Any] = {
            "id": job.get("payload", {}).get("provider_profile_id"),
            "name": job.get("payload", {}).get("provider", "injected provider"),
            "default_model": job.get("payload", {}).get("model"),
        }
        try:
            resolved_profile, provider = self.provider_resolver()
            if profile["id"] and resolved_profile.get("id") != profile["id"]:
                raise ValueError("任务使用的 Provider Profile 已变更或不可用，请创建新的重试任务。")
            profile = resolved_profile
            self.db.update_job(job_id, status="running", stage="materializing_media", progress=5)
            self.automation.update_resource_transcription(resource_id, "preparing", job_id=job_id)
            source = self._materialize(resource)
            chunks = self.automation.list_transcription_chunks(resource_id)
            missing_pending_media = any(
                chunk["status"] != "succeeded"
                and (
                    not chunk.get("media_path")
                    or not Path(chunk["media_path"]).is_file()
                    or Path(chunk["media_path"]).stat().st_size == 0
                )
                for chunk in chunks
            )
            if not chunks or missing_pending_media:
                # 真实 Profile 即使声明 long_audio，也统一走可恢复分片；能力标签不能
                # 代替可验证的大小限制。id=None 仅用于测试/嵌入式注入 Provider，保留
                # 其明确提供的整文件长音频契约，但未知时长仍必须先走媒体探测。
                injected_native_long_audio = (
                    profile.get("id") is None
                    and "long_audio" in profile.get("capabilities", [])
                    and bool(resource.get("duration_seconds"))
                )
                if injected_native_long_audio:
                    prepared = [
                        {
                            "start_seconds": 0.0,
                            "end_seconds": float(resource["duration_seconds"]),
                            "media_path": str(source),
                        }
                    ]
                else:
                    prepared = await asyncio.to_thread(
                        self.media_preparer.prepare,
                        resource_id,
                        source,
                        resource.get("duration_seconds"),
                    )
                chunks = self.automation.replace_transcription_chunks(resource_id, prepared)
            pending = [chunk for chunk in chunks if chunk["status"] != "succeeded"]
            total = max(1, len(chunks))
            failures = 0
            failure_details: list[str] = []
            for chunk in pending:
                current_job = self.db.get_job(job_id)
                if current_job["status"] == "cancelled":
                    self.automation.update_resource_transcription(resource_id, "cancelled")
                    return current_job
                progress = 12 + int(78 * chunk["position"] / total)
                self.db.update_job(job_id, stage=f"transcribing_chunk_{chunk['position'] + 1}_of_{total}", progress=progress)
                self.automation.update_resource_transcription(resource_id, "transcribing")
                self.automation.update_transcription_chunk(
                    chunk["id"], "running", increment_attempt=True
                )
                request_count += 1
                try:
                    segments = await self._transcribe_chunk(provider, chunk, resource, job_id)
                    offset_segments = [
                        {
                            "start_time": chunk["start_seconds"] + segment.start_time,
                            "end_time": chunk["start_seconds"] + segment.end_time,
                            "text": segment.text,
                        }
                        for segment in segments
                        if segment.text.strip()
                    ]
                    self.automation.update_transcription_chunk(
                        chunk["id"],
                        "succeeded",
                        segment_count=len(offset_segments),
                        segments=offset_segments,
                    )
                    self._save_successful_segments(resource_id)
                except TranscriptionCancelled:
                    # 运行中取消：本地进程已终止，成功分片保留为可续跑状态。
                    self.automation.update_transcription_chunk(chunk["id"], "pending")
                    self._save_successful_segments(resource_id)
                    self.automation.update_resource_transcription(resource_id, "cancelled")
                    self.automation.log_provider_call(
                        {
                            "operation": "transcription",
                            "provider_profile_id": profile.get("id"),
                            "provider_name": profile.get("name", "injected provider"),
                            "model": profile.get("default_model"),
                            "job_id": job_id,
                            "resource_id": resource_id,
                            "session_id": resource["session_id"],
                            "status": "cancelled",
                            "duration_ms": int((time.monotonic() - started) * 1000),
                            "request_count": request_count,
                            "cost_known": False,
                            "error_type": "cancelled_during_chunk",
                        }
                    )
                    return self.db.get_job(job_id)
                except Exception as exc:
                    failures += 1
                    failure_details.append(str(exc))
                    self.automation.update_transcription_chunk(chunk["id"], "failed", error=str(exc))
                    self._save_successful_segments(resource_id)
            chunks = self.automation.list_transcription_chunks(resource_id)
            successful = sum(chunk["status"] == "succeeded" for chunk in chunks)
            if failures:
                state = "partial" if successful else "failed"
                message = f"{failures} 个分片失败；已保留 {successful} 个成功分片，可直接重试"
                if failure_details:
                    message += f"。原因：{failure_details[0][:400]}"
                self.automation.update_resource_transcription(resource_id, state, error=message)
                result = self.db.update_job(job_id, status="failed", stage=state, progress=int(90 * successful / total), error=message)
                call_status = "failed"
            else:
                segments = self.db.list_transcript_segments(resource_id)
                self.score_refresher(resource["session_id"])
                self._suggest_title(resource["session_id"], segments)
                self.automation.update_resource_transcription(resource_id, "transcribed")
                result = self.db.update_job(
                    job_id,
                    status="succeeded",
                    stage="complete",
                    progress=100,
                    result={"segment_count": len(segments), "chunk_count": len(chunks)},
                )
                call_status = "succeeded"
            self.automation.log_provider_call(
                {
                    "operation": "transcription",
                    "provider_profile_id": profile.get("id"),
                    "provider_name": profile.get("name", "injected provider"),
                    "model": profile.get("default_model"),
                    "job_id": job_id,
                    "resource_id": resource_id,
                    "session_id": resource["session_id"],
                    "status": call_status,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "request_count": request_count,
                    "audio_minutes": (resource.get("duration_seconds") or 0) / 60 or None,
                    "cost_known": False,
                    "error_type": "partial_chunk_failure" if failures else None,
                }
            )
            return result
        except Exception as exc:
            self.automation.update_resource_transcription(resource_id, "failed", error=str(exc), job_id=job_id)
            result = self.db.update_job(job_id, status="failed", stage="failed", error=str(exc))
            self.automation.log_provider_call(
                {
                    "operation": "transcription",
                    "provider_profile_id": profile.get("id"),
                    "provider_name": profile.get("name", "injected provider"),
                    "model": profile.get("default_model"),
                    "job_id": job_id,
                    "resource_id": resource_id,
                    "session_id": resource["session_id"],
                    "status": "failed",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "request_count": request_count,
                    "audio_minutes": (resource.get("duration_seconds") or 0) / 60 or None,
                    "cost_known": False,
                    "error_type": type(exc).__name__,
                }
            )
            return result

    async def _transcribe_chunk(
        self,
        provider: TranscriptionProvider,
        chunk: dict[str, Any],
        resource: dict[str, Any],
        job_id: str,
    ) -> list[Any]:
        """转写单个分片，并在等待期间检查取消，避免本地长转写无法中断。"""

        task = asyncio.ensure_future(
            provider.transcribe(chunk["media_path"], resource.get("mime_type"))
        )
        while True:
            done, _ = await asyncio.wait({task}, timeout=self.cancel_poll_seconds)
            if done:
                return task.result()
            if self.db.get_job(job_id)["status"] == JobStatus.CANCELLED.value:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
                raise TranscriptionCancelled(
                    "转写在运行中被取消；本地进程已终止，成功分片已保留。"
                )

    def _materialize(self, resource: dict[str, Any]) -> Path:
        if resource.get("local_path"):
            return Path(resource["local_path"])
        if resource.get("storage_key"):
            return self.storage.materialize(
                StoredObject(provider=resource["storage_provider"], key=resource["storage_key"])
            )
        raise ValueError("该资源没有可读取的原始媒体文件；请检查存储或重新上传。")

    def _save_successful_segments(self, resource_id: str) -> None:
        merged: list[dict[str, Any]] = []
        for chunk in self.automation.list_transcription_chunks(resource_id):
            if chunk["status"] == "succeeded":
                merged.extend(chunk.get("segments") or [])
        merged.sort(key=lambda segment: (segment["start_time"], segment["end_time"], segment["text"]))
        deduplicated: list[dict[str, Any]] = []
        seen: set[tuple[float, float, str]] = set()
        for segment in merged:
            key = (round(segment["start_time"], 3), round(segment["end_time"], 3), segment["text"].strip())
            if key not in seen:
                seen.add(key)
                deduplicated.append(segment)
        self.db.save_transcript(resource_id, deduplicated)

    def _suggest_title(self, session_id: str, segments: list[dict[str, Any]]) -> None:
        automation = self.automation.session_automation(session_id)
        if automation["title_locked"] or not segments:
            return
        session = self.db.get_session(session_id)
        course = self.db.get_course(session["course_id"])
        timezone = self.automation.get_app_settings()["timezone"]
        date_part = local_date(
            timezone,
            parse_iso(session.get("starts_at") or session["created_at"]),
        ).isoformat()
        raw = " ".join(
            segment["text"].strip() for segment in segments[:5] if segment["text"].strip()
        )
        sentence = re.split(r"[。！？!?\n]", raw, maxsplit=1)[0]
        topic = re.sub(r"\s+", " ", sentence).strip(" ，,：:；;")
        prefix = re.compile(
            r"^(?:(?:今天|本节课|这节课)\s*)?(?:我们\s*)?(?:主要\s*)?(?:来\s*)?"
            r"(?:继续\s*)?(?:学习|讲解|介绍|讨论|复习)(?:了|一下)?[：:，,\s]*"
        )
        topic = prefix.sub("", topic).strip(" ，,：:；;")
        if topic.startswith(course["name"]):
            topic = topic[len(course["name"]) :].lstrip(" -—：:，,")
        topic = topic[:20].rstrip(" ，,：:；;")
        vague = {"课程内容", "课堂内容", "课堂笔记", "课程笔记", "本节课内容", "知识点", "复习"}
        significant_length = len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", topic))
        if not topic or topic in vague or significant_length < 3:
            return
        proposal = f"{course['name']}-{date_part}-{topic}"
        if significant_length >= 6 and not self.automation.open_review_item(
            "session_topic", "session", session_id
        ):
            self.automation.update_session_title(
                session_id,
                proposal,
                source="transcript_rule",
                confidence=0.82,
            )
            return
        self.automation.create_review_item(
            "session_topic",
            "session",
            session_id,
            "确认本节课主题",
            proposed_value=proposal,
            confidence=0.62,
            reasons=["来自转写开头的本地规则候选", "主题较短或已有用户决定，未自动覆盖标题"],
            navigation_path=f"/sessions/{session_id}",
        )
