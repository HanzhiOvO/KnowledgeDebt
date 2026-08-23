from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        data_dir=tmp_path,
        ai_provider="local_rule",
        asr_provider="local_rule",
        api_key=None,
        base_url="http://invalid",
        ai_model="local",
        asr_model="local",
    )
    return TestClient(create_app(settings))


def test_zero_config_local_mode_runs_full_mastery_loop(tmp_path: Path):
    client = make_client(tmp_path)

    settings = client.get("/settings/provider").json()
    assert settings["local_mode"] is True
    assert settings["configured"] is True
    assert settings["external_upload_requires_confirmation"] is False

    course = client.post("/courses", json={"name": "高等数学", "semester": "2026 Fall"}).json()
    session = client.post(
        f"/courses/{course['id']}/sessions",
        json={"title": "Lecture 12 · 中值定理", "notes": "用户缺席，从资料恢复。"},
    ).json()

    upload = client.post(
        f"/sessions/{session['id']}/resources/upload",
        data={"resource_type": "slides", "evidence_level": "official"},
        files={
            "file": (
                "lecture.txt",
                "拉格朗日中值定理：函数在闭区间连续、开区间可导，则存在一点使瞬时变化率等于平均变化率。",
                "text/plain",
            )
        },
    )
    assert upload.status_code == 201

    manifest = client.get(
        f"/sessions/{session['id']}/consent-manifest", params={"operation": "analysis"}
    ).json()
    assert manifest["external"] is False
    assert manifest["confirmation_required"] is False

    analyzed = client.post(f"/sessions/{session['id']}/analyze", json={})
    assert analyzed.status_code == 200
    detail = analyzed.json()
    assert [point["title"] for point in detail["knowledge_points"]] == ["拉格朗日中值定理"]
    assert detail["knowledge_points"][0]["confidence"] == "inferred"
    assert len(detail["learning_steps"]) == 1

    assessment = client.post(f"/sessions/{session['id']}/assessment", json={})
    assert assessment.status_code == 201
    questions = assessment.json()
    assert len(questions) == 2

    for question in questions:
        answered = client.post(
            f"/questions/{question['id']}/answer",
            json={"answer": "函数在闭区间连续、开区间可导，因此存在一点，其瞬时变化率等于平均变化率。"},
        )
        assert answered.status_code == 200
        assert answered.json()["evaluation"]["score"] >= 0.75

    completed = client.get(f"/sessions/{session['id']}").json()
    assert completed["status"] == "complete"
    assert all(debt["status"] == "mastered" for debt in completed["debts"])


def test_local_mode_does_not_fabricate_points_without_evidence(tmp_path: Path):
    client = make_client(tmp_path)
    course = client.post("/courses", json={"name": "没有资料"}).json()
    session = client.post(
        f"/courses/{course['id']}/sessions", json={"title": "只靠标题不能还原"}
    ).json()

    analyzed = client.post(f"/sessions/{session['id']}/analyze", json={})
    assert analyzed.status_code == 200
    assert analyzed.json()["knowledge_points"] == []
    assert analyzed.json()["reconstruction"]["inferred"] == []

    assessment = client.post(f"/sessions/{session['id']}/assessment", json={})
    assert assessment.status_code == 422


def test_web_landing_endpoints_for_resources_and_links(tmp_path: Path):
    client = make_client(tmp_path)
    course = client.post("/courses", json={"name": "资料与链接"}).json()
    session = client.post(
        f"/courses/{course['id']}/sessions", json={"title": "来源预览"}
    ).json()

    upload = client.post(
        f"/sessions/{session['id']}/resources/upload",
        data={"resource_type": "note", "evidence_level": "official"},
        files={"file": ("evidence.txt", "矩阵乘法要求左侧列数等于右侧行数。", "text/plain")},
    )
    assert upload.status_code == 201
    resource = upload.json()
    chunk_id = resource["chunks"][0]["id"]

    public_resource = client.get(f"/resources/{resource['id']}").json()
    assert public_resource["id"] == resource["id"]
    assert "local_path" not in public_resource
    assert "storage_key" not in public_resource
    assert "local_path" not in client.get(f"/sessions/{session['id']}").json()["resources"][0]

    raw = client.get(f"/resources/{resource['id']}/raw")
    assert raw.status_code == 200
    assert "矩阵乘法" in raw.text

    chunk = client.get(f"/chunks/{chunk_id}").json()
    assert chunk["text"].startswith("矩阵乘法")
    assert chunk["resource"]["name"] == "evidence.txt"
    assert "visual_path" not in chunk
    assert "metadata" not in chunk
    assert "embedding" not in chunk
    assert client.get(f"/chunks/{chunk_id}/visual").status_code == 404

    link = client.post(
        f"/sessions/{session['id']}/resources/link",
        json={"name": "课程主页", "url": "https://example.com/course", "notes": "补充阅读"},
    )
    assert link.status_code == 201
    assert link.json()["external_url"] == "https://example.com/course"

    updated = client.patch(
        f"/courses/{course['id']}/profile",
        json={
            "profile": {
                "classroom": 40,
                "official_session": 35,
                "course_context": 15,
                "supplementary": 10,
            }
        },
    )
    assert updated.status_code == 200


def test_auto_provider_resolution_follows_api_key(monkeypatch):
    monkeypatch.setenv("KNOWLEDGEDEBT_AI_PROVIDER", "auto")
    monkeypatch.setenv("KNOWLEDGEDEBT_ASR_PROVIDER", "auto")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from app.config import Settings

    local = Settings.from_env()
    assert local.ai_provider == "local_rule"
    assert local.asr_provider == "local_whisper"

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    remote = Settings.from_env()
    assert remote.ai_provider == "openai_compatible"
    # 语音转写默认仍走本地 Whisper；云端 ASR 需要显式开启。
    assert remote.asr_provider == "local_whisper"

    monkeypatch.setenv("KNOWLEDGEDEBT_LOCAL_ASR", "0")
    cloud_asr = Settings.from_env()
    assert cloud_asr.asr_provider == "openai_compatible"
