from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Database
from app.main import create_app
from app.models import (
    Confidence,
    EvaluationResult,
    KnowledgePointDraft,
    LearningStepDraft,
    QuestionDraft,
    ReconstructionDraft,
    RemediationDraft,
    SourceRef,
    TranscriptSegment,
)


class TestProvider:
    requires_external_upload = False

    async def analyze_session(self, session: dict, evidence: list[dict]) -> ReconstructionDraft:
        source = SourceRef(resource_id=evidence[0]["id"], label=evidence[0]["name"], locator="slide 1")
        point = KnowledgePointDraft(
            title="Lagrange mean value theorem",
            description="Conditions and conclusion",
            prerequisites=["Continuity", "Differentiability"],
            importance=5,
            expected_mastery_level=2,
            confidence=Confidence.CONFIRMED,
            sources=[source],
        )
        return ReconstructionDraft(
            title=session["title"],
            summary="The official slides cover the mean value theorem.",
            topics=[point.title],
            confirmed=[point.title],
            knowledge_points=[point],
            learning_path=[
                LearningStepDraft(
                    position=1,
                    title="Learn the conditions",
                    brief_explanation="Check continuity and differentiability.",
                    full_explanation="Continuity is required on the closed interval and differentiability on the open interval.",
                    knowledge_point_titles=[point.title],
                    estimated_minutes=5,
                    confidence=Confidence.CONFIRMED,
                    sources=[source],
                )
            ],
        )

    async def generate_questions(
        self, session: dict, evidence: list[dict], knowledge_points: list[dict]
    ) -> list[QuestionDraft]:
        point = knowledge_points[0]
        return [
            QuestionDraft(
                knowledge_point_title=point["title"],
                prompt="State the conditions of the Lagrange mean value theorem.",
                level="understanding",
                expected_mastery_level=2,
                reference_answer="Continuous on [a,b] and differentiable on (a,b).",
                rubric=["closed interval continuity", "open interval differentiability"],
                source_refs=[SourceRef(resource_id=evidence[0]["id"], label=evidence[0]["name"])],
            )
        ]

    async def evaluate_answer(self, question: dict, answer: str, evidence: list[dict]) -> EvaluationResult:
        return EvaluationResult(
            score=0.95,
            verdict="mastered",
            met_criteria=["closed interval continuity", "open interval differentiability"],
            feedback="Correct and complete.",
        )

    async def remediate(self, knowledge_point: dict, reason: str, evidence: list[dict]) -> RemediationDraft:
        return RemediationDraft(
            knowledge_point_title=knowledge_point["title"],
            diagnosis="The interval conditions were mixed up.",
            simpler_explanation="Check the endpoints for continuity, then the interior for differentiability.",
            analogy="A road includes its gates, while smooth driving is checked between them.",
            worked_example="f(x)=x² satisfies both conditions on [0,1].",
            quick_check="Where is differentiability required?",
        )

    async def transcribe(self, path: str, mime_type: str | None) -> list[TranscriptSegment]:
        return [TranscriptSegment(start_time=0, end_time=12, text="Today we study the mean value theorem.")]


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        data_dir=tmp_path,
        ai_provider="test",
        asr_provider="test",
        api_key=None,
        base_url="http://invalid",
        ai_model="test",
        asr_model="test",
    )
    provider = TestProvider()
    app = create_app(settings, Database(tmp_path / "test.sqlite3"), provider, provider)
    return TestClient(app)


def test_complete_course_to_mastery_flow(tmp_path: Path):
    client = make_client(tmp_path)
    course = client.post("/courses", json={"name": "高等数学", "semester": "2026 Fall"}).json()
    session = client.post(
        f"/courses/{course['id']}/sessions",
        json={"title": "Lecture 12 · 中值定理", "notes": "用户缺席"},
    ).json()
    upload = client.post(
        f"/sessions/{session['id']}/resources/upload",
        data={"resource_type": "slides", "evidence_level": "official"},
        files={"file": ("lecture.txt", "Lagrange theorem requires continuity and differentiability.", "text/plain")},
    )
    assert upload.status_code == 201
    resource = upload.json()
    assert resource["upload_state"] == "local_only"

    analyzed = client.post(f"/sessions/{session['id']}/analyze", json={}).json()
    assert analyzed["reconstruction"]["confirmed"] == ["Lagrange mean value theorem"]
    assert analyzed["debts"][0]["status"] == "unseen"
    assert analyzed["learning_steps"][0]["full_explanation"]

    remediation = client.post(
        f"/knowledge-points/{analyzed['knowledge_points'][0]['id']}/remediation",
        json={"reason": "I mixed up the intervals"},
    ).json()
    assert remediation["payload"]["quick_check"] == "Where is differentiability required?"

    questions = client.post(f"/sessions/{session['id']}/assessment", json={}).json()
    assert len(questions) == 1
    answer = client.post(
        f"/questions/{questions[0]['id']}/answer",
        json={"answer": "It is continuous on [a,b] and differentiable on (a,b)."},
    ).json()
    assert answer["debt_status"] == "mastered"
    assert answer["session_status"] == "complete"

    home = client.get("/home").json()
    assert home["open_debt_count"] == 0
    assert home["sessions"][0]["status"] == "complete"


def test_external_upload_requires_explicit_consent(tmp_path: Path):
    class ExternalTestProvider(TestProvider):
        requires_external_upload = True

    settings = Settings(tmp_path, "test", "test", None, "http://invalid", "test", "test")
    provider = ExternalTestProvider()
    client = TestClient(create_app(settings, Database(tmp_path / "consent.sqlite3"), provider, provider))
    course = client.post("/courses", json={"name": "Physics"}).json()
    session = client.post(f"/courses/{course['id']}/sessions", json={"title": "Lecture 1"}).json()
    response = client.post(f"/sessions/{session['id']}/analyze", json={"confirm_external_upload": False})
    assert response.status_code == 409
