from __future__ import annotations

from .database import Database
from .providers.base import AIProvider, ProviderOutputError, TranscriptionProvider
from .scoring import debt_status, learning_coverage, reconstruction_score, update_mastery


class KnowledgeService:
    def __init__(self, db: Database, ai: AIProvider, asr: TranscriptionProvider):
        self.db = db
        self.ai = ai
        self.asr = asr

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
        evidence = session["resources"]
        result = await self.ai.analyze_session(session, evidence)
        payload = result.model_dump(mode="json")
        self._validate_source_ids(payload, evidence)
        self.db.save_analysis(session_id, payload)
        self.refresh_scores(session_id)
        return self.db.get_session(session_id)

    async def make_quiz(self, session_id: str) -> list[dict]:
        session = self.db.get_session(session_id)
        points = session["knowledge_points"]
        if not points:
            raise ValueError("Analyze the session before generating an assessment")
        questions = await self.ai.generate_questions(session, session["resources"], points)
        allowed_points = {point["title"]: point["expected_mastery"] for point in points}
        for question in questions:
            expected = allowed_points.get(question.knowledge_point_title)
            if expected is None or question.expected_mastery_level > expected:
                raise ProviderOutputError("Provider returned an out-of-scope assessment question")
        self._validate_source_ids([q.model_dump(mode="json") for q in questions], session["resources"])
        return self.db.replace_questions(session_id, [q.model_dump(mode="json") for q in questions])

    async def evaluate(self, question_id: str, answer: str) -> dict:
        question = self.db.get_question(question_id)
        session = self.db.get_session(question["session_id"])
        evaluation = await self.ai.evaluate_answer(question, answer, session["resources"])
        point = next(item for item in session["knowledge_points"] if item["id"] == question["knowledge_point_id"])
        new_mastery = update_mastery(point["current_mastery"], evaluation.score, point["expected_mastery"])
        status = debt_status(new_mastery, point["expected_mastery"], attempted=True).value
        result = self.db.save_attempt_and_mastery(question_id, answer, evaluation.model_dump(), new_mastery, status)
        result["session_status"] = self.db.refresh_session_status(question["session_id"])
        return result

    async def remediate(self, point_id: str, reason: str) -> dict:
        point = self.db.get_knowledge_point(point_id)
        session = self.db.get_session(point["source_session_id"])
        draft = await self.ai.remediate(point, reason, session["resources"])
        self._validate_source_ids(draft.model_dump(mode="json"), session["resources"])
        return self.db.save_remediation(point_id, reason, draft.model_dump(mode="json"))

    @staticmethod
    def _validate_source_ids(payload: object, evidence: list[dict]) -> None:
        allowed = {item["id"] for item in evidence}

        def visit(value: object) -> None:
            if isinstance(value, dict):
                if "resource_id" in value and value["resource_id"] not in allowed:
                    raise ProviderOutputError("Provider returned a source reference that was not supplied")
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(payload)
