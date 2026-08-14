from __future__ import annotations

from typing import Protocol

from ..models import EvaluationResult, QuestionDraft, ReconstructionDraft, RemediationDraft, TranscriptSegment


class ProviderNotConfigured(RuntimeError):
    pass


class ProviderRequestError(RuntimeError):
    pass


class ProviderOutputError(RuntimeError):
    pass


class AIProvider(Protocol):
    requires_external_upload: bool

    async def analyze_session(self, session: dict, evidence: list[dict]) -> ReconstructionDraft: ...

    async def generate_questions(
        self, session: dict, evidence: list[dict], knowledge_points: list[dict]
    ) -> list[QuestionDraft]: ...

    async def evaluate_answer(self, question: dict, answer: str, evidence: list[dict]) -> EvaluationResult: ...

    async def remediate(self, knowledge_point: dict, reason: str, evidence: list[dict]) -> RemediationDraft: ...


class TranscriptionProvider(Protocol):
    requires_external_upload: bool

    async def transcribe(self, path: str, mime_type: str | None) -> list[TranscriptSegment]: ...
