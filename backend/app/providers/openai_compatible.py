from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from ..models import EvaluationResult, QuestionDraft, ReconstructionDraft, RemediationDraft, TranscriptSegment
from .base import ProviderNotConfigured, ProviderRequestError

T = TypeVar("T", bound=BaseModel)


SYSTEM_RULES = """You are the analysis engine for KnowledgeDebt, a university learning recovery tool.
Never claim supplementary or inferred material was taught in class. Preserve source references and evidence levels.
Stay within the supplied course evidence. Build a path that can teach a completely absent student from zero.
Never invent a timestamp, page, slide, chunk, or resource locator. Timeline timestamps must cite supplied transcript segments using locator_type=transcript and the segment's global_start/global_end values.
Mastery questions must test only the supplied course requirements and should take 3-5 minutes in total.
Return valid JSON only, matching the requested schema exactly. Do not use markdown fences."""


class QuestionList(BaseModel):
    questions: list[QuestionDraft]


class OpenAICompatibleProvider:
    requires_external_upload = True

    def __init__(
        self,
        api_key: str | None,
        base_url: str,
        ai_model: str,
        asr_model: str,
        embedding_model: str = "text-embedding-3-small",
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.ai_model = ai_model
        self.asr_model = asr_model
        self.embedding_model = embedding_model

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderNotConfigured("OPENAI_API_KEY is not configured")
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def _structured(self, model_type: type[T], prompt: str) -> T:
        schema = model_type.model_json_schema()
        body = {
            "model": self.ai_model,
            "messages": [
                {"role": "system", "content": SYSTEM_RULES},
                {"role": "user", "content": prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": model_type.__name__, "strict": True, "schema": schema},
            },
        }
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=body)
            if response.status_code == 400:
                fallback = {**body, "response_format": {"type": "json_object"}}
                response = await client.post(
                    f"{self.base_url}/chat/completions", headers=self._headers(), json=fallback
                )
            if not response.is_success:
                raise ProviderRequestError(f"AI provider returned HTTP {response.status_code}")
            payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content)
        return model_type.model_validate_json(content)

    @staticmethod
    def _evidence_text(evidence: list[dict], chunk_key: str = "retrieved_chunks") -> str:
        chunks = []
        for item in evidence:
            segments = [
                {
                    "id": segment["id"],
                    "resource_id": item["id"],
                    "global_start": segment["global_start"],
                    "global_end": segment["global_end"],
                    "text": segment["text"],
                }
                for segment in item.get("transcript_segments", [])
            ]
            document_chunks = [
                {
                    "chunk_id": chunk["id"],
                    "locator_type": chunk["locator_type"],
                    "page": chunk.get("page"),
                    "slide": chunk.get("slide"),
                    "text": chunk["text"],
                }
                for chunk in item.get(chunk_key, item.get("chunks", []))
            ]
            chunks.append(
                json.dumps(
                    {
                        "resource_id": item["id"],
                        "name": item["name"],
                        "type": item["type"],
                        "evidence_level": item["evidence_level"],
                        "transcript_segments": segments,
                        "document_chunks": document_chunks,
                        "text": "" if segments or document_chunks else item.get("extracted_text", "")[:4000],
                    },
                    ensure_ascii=False,
                )
            )
        return "\n".join(chunks)

    async def analyze_session(self, session: dict, evidence: list[dict]) -> ReconstructionDraft:
        prompt = f"""Analyze this Course Session and produce BOTH a classroom reconstruction and a distinct from-zero learning path.
The reconstruction answers what probably happened; the learning path answers how to genuinely learn it.
If evidence is insufficient, say so through inferred items and conservative confidence. SourceRef.resource_id and every structured locator must match supplied evidence exactly.
Session: {json.dumps({k: session.get(k) for k in ("title", "starts_at", "ends_at", "notes")}, ensure_ascii=False)}
Reconstruction evidence (classroom and official Session evidence ranked first):
{self._evidence_text(evidence, "reconstruction_chunks")}
From-zero learning evidence (official textbook, slides, and useful supplements ranked first):
{self._evidence_text(evidence, "learning_chunks")}
JSON schema: {json.dumps(ReconstructionDraft.model_json_schema(), ensure_ascii=False)}"""
        return await self._structured(ReconstructionDraft, prompt)

    async def generate_questions(
        self, session: dict, evidence: list[dict], knowledge_points: list[dict]
    ) -> list[QuestionDraft]:
        prompt = f"""Create 3-5 high-information adaptive mastery questions for this session. Cover recall, understanding, and application as appropriate.
Each question may cover one or more tightly related knowledge_point_titles, but every title must exactly match the supplied list. Cite real supplied locators and do not exceed any covered point's expected mastery.
Also create a reference answer and explicit rubric criteria. Reject tempting but out-of-scope material.
Knowledge points: {json.dumps(knowledge_points, ensure_ascii=False)}
Course evidence: {self._evidence_text(evidence)}
JSON schema: {json.dumps(QuestionList.model_json_schema(), ensure_ascii=False)}"""
        return (await self._structured(QuestionList, prompt)).questions

    async def evaluate_answer(self, question: dict, answer: str, evidence: list[dict]) -> EvaluationResult:
        prompt = f"""Evaluate the student's answer semantically, not by string equality. Check each rubric item, logic, and missing conditions.
Do not penalize equivalent wording. Do not require anything outside the cited evidence.
Return one point_results entry for every knowledge point covered by the question, with an evidence type that reflects what the answer demonstrated.
Question: {json.dumps(question, ensure_ascii=False)}
Student answer: {answer}
Relevant evidence: {self._evidence_text(evidence)}
JSON schema: {json.dumps(EvaluationResult.model_json_schema(), ensure_ascii=False)}"""
        return await self._structured(EvaluationResult, prompt)

    async def remediate(self, knowledge_point: dict, reason: str, evidence: list[dict]) -> RemediationDraft:
        prompt = f"""Teach only this weak knowledge point in a different, more foundational way.
Diagnose the likely gap from the student's reason. Include a compact analogy, one worked example, and one quick self-check.
Stay within the expected mastery level and supplied evidence. Do not expand into unrelated material.
Knowledge point: {json.dumps(knowledge_point, ensure_ascii=False)}
Student reason: {reason}
Course evidence: {self._evidence_text(evidence)}
JSON schema: {json.dumps(RemediationDraft.model_json_schema(), ensure_ascii=False)}"""
        return await self._structured(RemediationDraft, prompt)

    async def transcribe(self, path: str, mime_type: str | None) -> list[TranscriptSegment]:
        if not self.api_key:
            raise ProviderNotConfigured("OPENAI_API_KEY is not configured")
        headers = {"Authorization": f"Bearer {self.api_key}"}
        file_path = Path(path)
        async with httpx.AsyncClient(timeout=600) as client, file_path.open("rb") as handle:
            response = await client.post(
                f"{self.base_url}/audio/transcriptions",
                headers=headers,
                data={"model": self.asr_model, "response_format": "verbose_json"},
                files={"file": (file_path.name, handle, mime_type or "application/octet-stream")},
            )
            if not response.is_success:
                raise ProviderRequestError(f"ASR provider returned HTTP {response.status_code}")
            payload: dict[str, Any] = response.json()
        segments = payload.get("segments") or []
        if not segments:
            return [
                TranscriptSegment(
                    start_time=0, end_time=float(payload.get("duration", 0)), text=payload.get("text", "")
                )
            ]
        return [
            TranscriptSegment(
                start_time=float(item.get("start", 0)), end_time=float(item.get("end", 0)), text=item["text"].strip()
            )
            for item in segments
            if item.get("text", "").strip()
        ]

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        body = {"model": self.embedding_model, "input": texts}
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(f"{self.base_url}/embeddings", headers=self._headers(), json=body)
            if not response.is_success:
                raise ProviderRequestError(f"Embedding provider returned HTTP {response.status_code}")
            payload = response.json()
        return [item["embedding"] for item in sorted(payload["data"], key=lambda item: item["index"])]
