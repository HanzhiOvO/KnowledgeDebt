from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from ..models import TranscriptSegment
from .base import ProviderNotConfigured, ProviderOutputError, ProviderRequestError

DEFAULT_ACADEMIC_PROMPT = (
    "以下是大学课程录音，使用简体中文或英文，包含数学、计算机、物理、化学、"
    "经济、法律、医学等常见学科术语。"
)


class LocalWhisperProvider:
    """Local Chinese/English speech recognition via faster-whisper.

    The model is downloaded lazily on the first transcription job and cached below
    the project data directory. No audio leaves the server when this provider is
    selected.
    """

    requires_external_upload = False
    name = "local_whisper"

    def __init__(
        self,
        model_name: str = "small",
        device: str = "auto",
        compute_type: str = "auto",
        language: str | None = None,
        download_root: Path | str | None = None,
        initial_prompt: str | None = None,
    ):
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.initial_prompt = initial_prompt if initial_prompt is not None else DEFAULT_ACADEMIC_PROMPT
        self.download_root = Path(download_root) if download_root else Path.cwd() / "data" / "models" / "whisper"
        self.download_root.mkdir(parents=True, exist_ok=True)
        self._model = None

    @staticmethod
    def available() -> bool:
        try:
            return importlib.util.find_spec("faster_whisper") is not None
        except (ImportError, ValueError):
            return False

    def _load(self):
        if self._model is not None:
            return self._model
        if not self.available():
            raise ProviderNotConfigured(
                "本地语音转写未安装。请运行："
                ".venv/bin/pip install -r backend/requirements-local-asr.txt "
                "（或使用 ./start.sh 自动安装）"
            )
        hf_home = self.download_root / "huggingface"
        hf_home.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(hf_home))
        os.environ.setdefault("HF_HUB_CACHE", str(hf_home / "hub"))
        # Plain HTTP resume is more portable than the xet protocol on restricted
        # self-hosted machines and avoids writable-cache assumptions in $HOME.
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - exercised only on broken installs
            raise ProviderNotConfigured(
                "faster-whisper 安装不完整，请重新安装 backend/requirements-local-asr.txt"
            ) from exc

        try:
            device = self.device
            if device == "auto":
                try:
                    from ctranslate2 import get_cuda_device_count

                    device = "cuda" if get_cuda_device_count() > 0 else "cpu"
                except Exception:
                    device = "cpu"
            compute_type = self.compute_type
            if compute_type == "auto":
                compute_type = "float16" if device == "cuda" else "int8"
            self._model = WhisperModel(
                self.model_name,
                device=device,
                compute_type=compute_type,
                download_root=str(self.download_root),
            )
        except Exception as exc:
            if isinstance(exc, ProviderNotConfigured):
                raise
            raise ProviderRequestError(f"本地 Whisper 模型加载失败：{exc}") from exc
        return self._model

    async def transcribe(
        self,
        path: str,
        mime_type: str | None,
        initial_prompt: str | None = None,
    ) -> list[TranscriptSegment]:
        del mime_type
        prompt = "；".join(filter(None, [self.initial_prompt, initial_prompt])) or None
        try:
            model = self._load()
            segments, _info = model.transcribe(
                path,
                language=self.language,
                vad_filter=True,
                beam_size=1,
                condition_on_previous_text=False,
                initial_prompt=prompt,
            )
            result = [
                TranscriptSegment(
                    start_time=float(segment.start),
                    end_time=float(segment.end),
                    text=str(segment.text).strip(),
                )
                for segment in segments
                if str(segment.text).strip()
            ]
        except ProviderNotConfigured:
            raise
        except Exception as exc:
            raise ProviderRequestError(f"本地语音转写失败：{exc}") from exc
        if not result:
            raise ProviderOutputError("本地 Whisper 未识别到有效语音，请检查录音内容或麦克风音量。")
        return result
