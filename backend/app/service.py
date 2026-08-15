from __future__ import annotations

import re

from .database import Database
from .models import JobKind, JobStatus
from .providers.base import AIProvider, EmbeddingProvider, ProviderOutputError, TranscriptionProvider
from .providers.hash_embedding import HashEmbeddingProvider
from .retrieval import RetrievalPolicy, SessionRetriever
from .scoring import aggregate_mastery, debt_status, learning_coverage, reconstruction_score


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
            for title in question.knowledge_point_titles:
                expected = allowed_points.get(title)
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
        points_by_id = {point["id"]: point for point in session["knowledge_points"]}
        points_by_title = {point["title"]: point for point in session["knowledge_points"]}
        point_ids = question.get("knowledge_point_ids") or [question["knowledge_point_id"]]
        evaluated = {item.knowledge_point_title: item for item in evaluation.point_results}
        evidence_results: list[dict] = []
        evidence_type = self._evidence_type(question["level"])
        for point_id in point_ids:
            point = points_by_id.get(point_id)
            if not point:
                continue
            point_result = evaluated.get(point["title"])
            evidence_results.append(
                {
                    "knowledge_point_id": point_id,
                    "evidence_type": point_result.evidence_type.value if point_result else evidence_type,
                    "score": point_result.score if point_result else evaluation.score,
                    "weight": 1.0,
                    "metadata": {"question_type": question.get("question_type", "diagnostic")},
                }
            )
        attempt = self.db.save_attempt(question_id, answer, evaluation.model_dump(mode="json"))
        saved_evidence = self.db.add_mastery_evidence(attempt["id"], question_id, evidence_results)
        updates: list[dict] = []
        for point_id in point_ids:
            point = points_by_id.get(point_id)
            if not point:
                continue
            mastery = aggregate_mastery(self.db.list_mastery_evidence(point_id), point["expected_mastery"])
            status = debt_status(mastery, point["expected_mastery"], attempted=True).value
            self.db.update_point_mastery(point_id, mastery, status)
            updates.append(
                {"knowledge_point_id": point_id, "title": point["title"], "mastery": mastery, "status": status}
            )
        weak_points = [
            points_by_title[item.knowledge_point_title]
            for item in evaluation.point_results
            if item.score < 0.75 and item.knowledge_point_title in points_by_title
        ]
        if not evaluation.point_results and evaluation.score < 0.75:
            weak_points = [points_by_id[item] for item in point_ids if item in points_by_id]
        follow_ups = await self._make_follow_up(session, question, answer, evaluation, weak_points, evidence)
        result = {
            **attempt,
            "mastery_evidence": saved_evidence,
            "mastery_updates": updates,
            "new_mastery": updates[0]["mastery"] if len(updates) == 1 else None,
            "debt_status": updates[0]["status"] if len(updates) == 1 else None,
            "follow_up_questions": follow_ups,
        }
        result["session_status"] = self.db.refresh_session_status(question["session_id"])
        return result

    async def _make_follow_up(
        self,
        session: dict,
        question: dict,
        answer: str,
        evaluation: object,
        weak_points: list[dict],
        evidence: list[dict],
    ) -> list[dict]:
        if not weak_points:
            return []
        follow_ups = await self.ai.generate_questions(session, evidence, weak_points)
        allowed = {point["title"]: point["expected_mastery"] for point in weak_points}
        payloads: list[dict] = []
        for follow_up in follow_ups[:2]:
            if not all(title in allowed for title in follow_up.knowledge_point_titles):
                raise ProviderOutputError("Provider returned an out-of-scope follow-up question")
            payload = follow_up.model_dump(mode="json")
            payload["question_type"] = "follow_up"
            payloads.append(payload)
        self._validate_sources(payloads, evidence)
        del answer, evaluation
        return self.db.append_questions(session["id"], payloads, parent_question_id=question["id"])

    @staticmethod
    def _evidence_type(level: str) -> str:
        normalized = level.lower()
        if normalized in {"recall", "remember"}:
            return "recall"
        if normalized in {"application", "apply", "problem_solving"}:
            return "application"
        if normalized in {"transfer", "synthesis"}:
            return "transfer"
        return "understanding"

    async def run_job(self, job_id: str) -> dict:
        job = self.db.get_job(job_id)
        if job["status"] == JobStatus.CANCELLED.value:
            return job
        try:
            self.db.update_job(job_id, status=JobStatus.RUNNING.value, stage="preparing", progress=5)
            if job["kind"] == JobKind.ANALYSIS.value:
                self.db.update_job(job_id, stage="retrieving_evidence", progress=20)
                result = await self.analyze(job["session_id"])
                summary = {
                    "knowledge_point_count": len(result["knowledge_points"]),
                    "debt_count": len(result["debts"]),
                }
            elif job["kind"] == JobKind.ASSESSMENT.value:
                self.db.update_job(job_id, stage="generating_questions", progress=35)
                result = await self.make_quiz(job["session_id"])
                summary = {"question_count": len(result)}
            else:
                raise ValueError(f"unsupported job kind: {job['kind']}")
            return self.db.update_job(
                job_id,
                status=JobStatus.SUCCEEDED.value,
                stage="complete",
                progress=100,
                result=summary,
            )
        except Exception as exc:
            return self.db.update_job(
                job_id,
                status=JobStatus.FAILED.value,
                stage="failed",
                error=str(exc),
            )

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
