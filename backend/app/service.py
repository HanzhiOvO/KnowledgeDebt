from __future__ import annotations

import re

from .database import Database
from .providers.base import AIProvider, EmbeddingProvider, ProviderOutputError, TranscriptionProvider
from .providers.hash_embedding import HashEmbeddingProvider
from .retrieval import RetrievalPolicy, SessionRetriever
from .scoring import debt_status, learning_coverage, reconstruction_score, update_mastery


class KnowledgeService:
    def __init__(
        self,
        db: Database,
        ai: AIProvider,
        asr: TranscriptionProvider,
        embeddings: EmbeddingProvider | None = None,
    ):
        self.db = db
        self.ai = ai
        self.asr = asr
        self.embeddings = embeddings or HashEmbeddingProvider()
        self.retriever = SessionRetriever(db, self.embeddings)

    def refresh_scores(self, session_id: str) -> tuple[int, int]:
        session = self.db.get_session(session_id)
        profile = self.db.get_course(session["course_id"])["profile"]
        resources = session["resources"]
        reconstruction = reconstruction_score(resources, profile)
        coverage = learning_coverage(resources)
        self.db.update_session_scores(session_id, reconstruction, coverage)
        return reconstruction, coverage

    async def analyze(self, session_id: str) -> dict:
        session = self.db.get_session(session_id)
        query = " ".join((session["title"], session.get("notes", "")))
        reconstruction_chunks = await self.retriever.retrieve(
            session_id, query, RetrievalPolicy.RECONSTRUCTION
        )
        learning_chunks = await self.retriever.retrieve(session_id, query, RetrievalPolicy.LEARNING)
        evidence = self._dual_policy_evidence(session["resources"], reconstruction_chunks, learning_chunks)
        result = await self.ai.analyze_session(session, evidence)
        payload = result.model_dump(mode="json")
        self._validate_sources(payload, evidence)
        self._validate_timeline(payload, evidence)
        self.db.save_analysis(session_id, payload)
        self.refresh_scores(session_id)
        return self.db.get_session(session_id)

    async def make_quiz(self, session_id: str) -> list[dict]:
        session = self.db.get_session(session_id)
        points = session["knowledge_points"]
        if not points:
            raise ValueError("Analyze the session before generating an assessment")
        query = " ".join(point["title"] for point in points)
        chunks = await self.retriever.retrieve(session_id, query, RetrievalPolicy.LEARNING)
        evidence = self.retriever.attach_to_resources(session["resources"], chunks)
        questions = await self.ai.generate_questions(session, evidence, points)
        allowed_points = {point["title"]: point["expected_mastery"] for point in points}
        for question in questions:
            expected = allowed_points.get(question.knowledge_point_title)
            if expected is None or question.expected_mastery_level > expected:
                raise ProviderOutputError("Provider returned an out-of-scope assessment question")
        self._validate_sources([q.model_dump(mode="json") for q in questions], evidence)
        return self.db.replace_questions(session_id, [q.model_dump(mode="json") for q in questions])

    async def evaluate(self, question_id: str, answer: str) -> dict:
        question = self.db.get_question(question_id)
        session = self.db.get_session(question["session_id"])
        chunks = await self.retriever.retrieve(
            session["id"], question["prompt"], RetrievalPolicy.LEARNING
        )
        evidence = self.retriever.attach_to_resources(session["resources"], chunks)
        evaluation = await self.ai.evaluate_answer(question, answer, evidence)
        point = next(item for item in session["knowledge_points"] if item["id"] == question["knowledge_point_id"])
        new_mastery = update_mastery(point["current_mastery"], evaluation.score, point["expected_mastery"])
        status = debt_status(new_mastery, point["expected_mastery"], attempted=True).value
        result = self.db.save_attempt_and_mastery(question_id, answer, evaluation.model_dump(), new_mastery, status)
        result["session_status"] = self.db.refresh_session_status(question["session_id"])
        return result

    async def remediate(self, point_id: str, reason: str) -> dict:
        point = self.db.get_knowledge_point(point_id)
        session = self.db.get_session(point["source_session_id"])
        chunks = await self.retriever.retrieve(
            session["id"], f"{point['title']} {point['description']} {reason}", RetrievalPolicy.LEARNING
        )
        evidence = self.retriever.attach_to_resources(session["resources"], chunks)
        draft = await self.ai.remediate(point, reason, evidence)
        self._validate_sources(draft.model_dump(mode="json"), evidence)
        return self.db.save_remediation(point_id, reason, draft.model_dump(mode="json"))

    def _dual_policy_evidence(
        self, resources: list[dict], reconstruction_chunks: list[dict], learning_chunks: list[dict]
    ) -> list[dict]:
        reconstruction = {
            item["id"]: item
            for item in self.retriever.attach_to_resources(resources, reconstruction_chunks)
        }
        learning = {
            item["id"]: item for item in self.retriever.attach_to_resources(resources, learning_chunks)
        }
        selected: list[dict] = []
        for resource in resources:
            if resource["id"] not in reconstruction and resource["id"] not in learning:
                continue
            selected.append(
                {
                    **resource,
                    "reconstruction_chunks": reconstruction.get(resource["id"], {}).get(
                        "retrieved_chunks", []
                    ),
                    "learning_chunks": learning.get(resource["id"], {}).get("retrieved_chunks", []),
                }
            )
        return selected

    @staticmethod
    def _validate_sources(payload: object, evidence: list[dict]) -> None:
        allowed = {item["id"]: item for item in evidence}

        def validate_reference(value: dict) -> None:
            resource = allowed.get(value.get("resource_id"))
            if resource is None:
                raise ProviderOutputError("Provider returned a source reference that was not supplied")
            locator_type = value.get("locator_type")
            if locator_type == "transcript":
                start, end = value.get("start_time"), value.get("end_time")
                if start is None or end is None:
                    raise ProviderOutputError("Transcript source reference is missing its time range")
                matches = [
                    segment
                    for segment in resource.get("transcript_segments", [])
                    if abs(float(segment["global_start"]) - float(start)) < 1e-3
                    and abs(float(segment["global_end"]) - float(end)) < 1e-3
                ]
                if not matches:
                    raise ProviderOutputError("Provider returned a transcript time range that does not exist")
            elif locator_type == "page":
                pages = {int(item) for item in re.findall(r"\[PDF page (\d+)\]", resource.get("extracted_text", ""))}
                if value.get("page") not in pages:
                    raise ProviderOutputError("Provider returned a PDF page that does not exist")
            elif locator_type == "slide":
                slides = {int(item) for item in re.findall(r"\[PPT slide (\d+)\]", resource.get("extracted_text", ""))}
                if value.get("slide") not in slides:
                    raise ProviderOutputError("Provider returned a slide that does not exist")
            elif locator_type == "chunk":
                chunk_ids = {item["id"] for item in resource.get("chunks", [])}
                if value.get("chunk_id") not in chunk_ids:
                    raise ProviderOutputError("Provider returned a document chunk that does not exist")

        def visit(value: object) -> None:
            if isinstance(value, dict):
                if "resource_id" in value:
                    validate_reference(value)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(payload)

    @classmethod
    def _validate_timeline(cls, payload: dict, evidence: list[dict]) -> None:
        for item in payload.get("timeline", []):
            start, end = item.get("start_time"), item.get("end_time")
            if start is None and end is None:
                continue
            if start is None or end is None or end <= start:
                raise ProviderOutputError("Timeline item has an invalid time range")
            transcript_refs = [
                source
                for source in item.get("sources", [])
                if source.get("locator_type") == "transcript"
            ]
            if not any(
                abs(float(source["start_time"]) - float(start)) < 1e-3
                and abs(float(source["end_time"]) - float(end)) < 1e-3
                for source in transcript_refs
            ):
                raise ProviderOutputError("Timeline timestamps must match a cited transcript segment")
