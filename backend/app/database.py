from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .models import DEFAULT_PROFILE, CourseCreate, SessionCreate, utc_now

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS courses (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
  semester TEXT NOT NULL, teacher TEXT, schedule TEXT, profile_json TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  title TEXT NOT NULL, starts_at TEXT, ends_at TEXT, notes TEXT NOT NULL,
  reconstruction_score INTEGER NOT NULL DEFAULT 0,
  learning_coverage INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resources (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  type TEXT NOT NULL, evidence_level TEXT NOT NULL, name TEXT NOT NULL, mime_type TEXT,
  local_path TEXT, external_url TEXT, extracted_text TEXT NOT NULL DEFAULT '',
  coverage REAL NOT NULL DEFAULT 1, quality REAL NOT NULL DEFAULT 1,
  relevance REAL NOT NULL DEFAULT 1, duration_seconds REAL,
  start_offset REAL, end_offset REAL, session_duration REAL,
  capture_range_json TEXT NOT NULL DEFAULT '[]',
  upload_state TEXT NOT NULL DEFAULT 'local_only', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transcript_segments (
  id TEXT PRIMARY KEY, resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  start_time REAL NOT NULL, end_time REAL NOT NULL,
  global_start REAL NOT NULL, global_end REAL NOT NULL,
  text TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reconstructions (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE REFERENCES sessions(id) ON DELETE CASCADE,
  payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_points (
  id TEXT PRIMARY KEY, course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  source_session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  title TEXT NOT NULL, description TEXT NOT NULL, prerequisites_json TEXT NOT NULL,
  importance INTEGER NOT NULL, expected_mastery INTEGER NOT NULL,
  current_mastery REAL NOT NULL DEFAULT 0, confidence TEXT NOT NULL,
  sources_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(source_session_id, title)
);
CREATE TABLE IF NOT EXISTS debts (
  id TEXT PRIMARY KEY, knowledge_point_id TEXT NOT NULL UNIQUE REFERENCES knowledge_points(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  current_mastery REAL NOT NULL, target_mastery INTEGER NOT NULL,
  status TEXT NOT NULL, priority INTEGER NOT NULL, estimated_minutes INTEGER NOT NULL,
  blocks_next_session INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS learning_steps (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  position INTEGER NOT NULL, title TEXT NOT NULL, brief_explanation TEXT NOT NULL,
  full_explanation TEXT NOT NULL, knowledge_point_ids_json TEXT NOT NULL,
  estimated_minutes INTEGER NOT NULL, confidence TEXT NOT NULL, sources_json TEXT NOT NULL,
  completed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS questions (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  knowledge_point_id TEXT NOT NULL REFERENCES knowledge_points(id) ON DELETE CASCADE,
  prompt TEXT NOT NULL, level TEXT NOT NULL, expected_mastery INTEGER NOT NULL,
  reference_answer TEXT NOT NULL, rubric_json TEXT NOT NULL, sources_json TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
  id TEXT PRIMARY KEY, question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  answer TEXT NOT NULL, evaluation_json TEXT NOT NULL, score REAL NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS remediations (
  id TEXT PRIMARY KEY, knowledge_point_id TEXT NOT NULL REFERENCES knowledge_points(id) ON DELETE CASCADE,
  reason TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_course ON sessions(course_id);
CREATE INDEX IF NOT EXISTS idx_resources_session ON resources(session_id);
CREATE INDEX IF NOT EXISTS idx_debts_session ON debts(session_id);
"""


def _id() -> str:
    return str(uuid.uuid4())


def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for key in tuple(result):
        if key.endswith("_json"):
            result[key.removesuffix("_json")] = json.loads(result.pop(key))
    for key in ("completed", "active", "blocks_next_session"):
        if key in result:
            result[key] = bool(result[key])
    return result


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate_legacy_schema(conn)

    @staticmethod
    def _migrate_legacy_schema(conn: sqlite3.Connection) -> None:
        resource_columns = {row["name"] for row in conn.execute("PRAGMA table_info(resources)")}
        resource_migrations = {
            "start_offset": "ALTER TABLE resources ADD COLUMN start_offset REAL",
            "end_offset": "ALTER TABLE resources ADD COLUMN end_offset REAL",
            "session_duration": "ALTER TABLE resources ADD COLUMN session_duration REAL",
            "capture_range_json": "ALTER TABLE resources ADD COLUMN capture_range_json TEXT NOT NULL DEFAULT '[]'",
        }
        for column, statement in resource_migrations.items():
            if column not in resource_columns:
                conn.execute(statement)

        transcript_columns = {row["name"] for row in conn.execute("PRAGMA table_info(transcript_segments)")}
        if "global_start" not in transcript_columns:
            conn.execute("ALTER TABLE transcript_segments ADD COLUMN global_start REAL")
        if "global_end" not in transcript_columns:
            conn.execute("ALTER TABLE transcript_segments ADD COLUMN global_end REAL")
        conn.execute(
            """
            UPDATE transcript_segments
            SET global_start = start_time + COALESCE(
                  (SELECT start_offset FROM resources WHERE resources.id = transcript_segments.resource_id), 0
                ),
                global_end = end_time + COALESCE(
                  (SELECT start_offset FROM resources WHERE resources.id = transcript_segments.resource_id), 0
                )
            WHERE global_start IS NULL OR global_end IS NULL
            """
        )

        for row in conn.execute("SELECT id, profile_json FROM courses").fetchall():
            profile = json.loads(row["profile_json"])
            if not set(DEFAULT_PROFILE).issubset(profile):
                conn.execute(
                    "UPDATE courses SET profile_json=?, updated_at=? WHERE id=?",
                    (json.dumps(DEFAULT_PROFILE), utc_now(), row["id"]),
                )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def create_course(self, payload: CourseCreate) -> dict[str, Any]:
        now, row_id = utc_now(), _id()
        profile = DEFAULT_PROFILE.copy()
        profile.update(payload.profile)
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO courses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row_id,
                    payload.name,
                    payload.description,
                    payload.semester,
                    payload.teacher,
                    payload.schedule,
                    json.dumps(profile),
                    now,
                    now,
                ),
            )
        return self.get_course(row_id)

    def list_courses(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM courses ORDER BY updated_at DESC").fetchall()
        return [_decode(row) for row in rows]  # type: ignore[misc]

    def get_course(self, course_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
        if not row:
            raise KeyError("course")
        return _decode(row)  # type: ignore[return-value]

    def update_course_profile(self, course_id: str, profile: dict[str, float]) -> dict[str, Any]:
        course = self.get_course(course_id)
        merged = course["profile"]
        merged.update(profile)
        total = sum(merged.get(channel, 0.0) for channel in DEFAULT_PROFILE)
        if abs(total - 100.0) > 1e-6:
            raise ValueError("evidence channel weights must total 100")
        with self.connect() as conn:
            conn.execute(
                "UPDATE courses SET profile_json=?, updated_at=? WHERE id=?",
                (json.dumps(merged), utc_now(), course_id),
            )
        return self.get_course(course_id)

    def create_session(self, course_id: str, payload: SessionCreate) -> dict[str, Any]:
        self.get_course(course_id)
        now, row_id = utc_now(), _id()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, course_id, title, starts_at, ends_at, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row_id, course_id, payload.title, payload.starts_at, payload.ends_at, payload.notes, now, now),
            )
        return self.get_session(row_id)

    def list_sessions(self, course_id: str | None = None) -> list[dict[str, Any]]:
        query, args = "SELECT * FROM sessions", ()
        if course_id:
            query, args = query + " WHERE course_id = ?", (course_id,)
        with self.connect() as conn:
            rows = conn.execute(query + " ORDER BY starts_at DESC, created_at DESC", args).fetchall()
        return [_decode(row) for row in rows]  # type: ignore[misc]

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            raise KeyError("session")
        result = _decode(row)  # type: ignore[assignment]
        result["resources"] = self.list_resources(session_id)
        result["knowledge_points"] = self.list_knowledge_points(session_id)
        result["debts"] = self.list_debts(session_id)
        result["learning_steps"] = self.list_learning_steps(session_id)
        result["reconstruction"] = self.get_reconstruction(session_id)
        return result

    def add_resource(self, session_id: str, **values: Any) -> dict[str, Any]:
        self.get_session(session_id)
        now, row_id = utc_now(), _id()
        columns = {
            "id": row_id,
            "session_id": session_id,
            "type": values["type"],
            "evidence_level": values["evidence_level"],
            "name": values["name"],
            "mime_type": values.get("mime_type"),
            "local_path": values.get("local_path"),
            "external_url": values.get("external_url"),
            "extracted_text": values.get("extracted_text", ""),
            "coverage": values.get("coverage", 1.0),
            "quality": values.get("quality", 1.0),
            "relevance": values.get("relevance", 1.0),
            "duration_seconds": values.get("duration_seconds"),
            "start_offset": values.get("start_offset"),
            "end_offset": values.get("end_offset"),
            "session_duration": values.get("session_duration"),
            "capture_range_json": json.dumps(values.get("capture_range", [])),
            "upload_state": "local_only",
            "created_at": now,
            "updated_at": now,
        }
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO resources ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                tuple(columns.values()),
            )
        return self.get_resource(row_id)

    def get_resource(self, resource_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM resources WHERE id = ?", (resource_id,)).fetchone()
        if not row:
            raise KeyError("resource")
        result = _decode(row)  # type: ignore[assignment]
        result["transcript_segments"] = self.list_transcript_segments(resource_id)
        return result  # type: ignore[return-value]

    def list_resources(self, session_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM resources WHERE session_id = ? ORDER BY created_at", (session_id,)
            ).fetchall()
        resources = [_decode(row) for row in rows]  # type: ignore[misc]
        for resource in resources:
            resource["transcript_segments"] = self.list_transcript_segments(resource["id"])
        return resources

    def list_transcript_segments(self, resource_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM transcript_segments WHERE resource_id=? ORDER BY global_start, start_time",
                (resource_id,),
            ).fetchall()
        return [_decode(row) for row in rows]  # type: ignore[misc]

    def update_resource_quality(
        self, resource_id: str, coverage: float, quality: float, relevance: float
    ) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                "UPDATE resources SET coverage=?, quality=?, relevance=?, updated_at=? WHERE id=?",
                (coverage, quality, relevance, utc_now(), resource_id),
            )
        return self.get_resource(resource_id)

    def save_transcript(self, resource_id: str, segments: list[dict[str, Any]]) -> None:
        resource = self.get_resource(resource_id)
        offset = float(resource.get("start_offset") or 0.0)
        now = utc_now()
        with self.connect() as conn:
            conn.execute("DELETE FROM transcript_segments WHERE resource_id = ?", (resource_id,))
            conn.executemany(
                """INSERT INTO transcript_segments
                   (id, resource_id, start_time, end_time, global_start, global_end, text, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        _id(),
                        resource_id,
                        s["start_time"],
                        s["end_time"],
                        offset + s["start_time"],
                        offset + s["end_time"],
                        s["text"],
                        now,
                    )
                    for s in segments
                ],
            )
            text = "\n".join(s["text"] for s in segments)
            conn.execute(
                "UPDATE resources SET extracted_text=?, upload_state='processed', updated_at=? WHERE id=?",
                (text, now, resource_id),
            )

    def save_analysis(self, session_id: str, payload: dict[str, Any]) -> None:
        session = self.get_session(session_id)
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO reconstructions VALUES (?, ?, ?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at",
                (_id(), session_id, json.dumps(payload, ensure_ascii=False), now, now),
            )
            conn.execute("DELETE FROM learning_steps WHERE session_id = ?", (session_id,))
            title_to_id: dict[str, str] = {}
            for point in payload["knowledge_points"]:
                point_id = _id()
                previous = conn.execute(
                    "SELECT id, current_mastery FROM knowledge_points WHERE source_session_id=? AND title=?",
                    (session_id, point["title"]),
                ).fetchone()
                if previous:
                    point_id, current = previous["id"], previous["current_mastery"]
                    conn.execute(
                        "UPDATE knowledge_points SET description=?, prerequisites_json=?, importance=?, expected_mastery=?, confidence=?, sources_json=?, updated_at=? WHERE id=?",
                        (
                            point["description"],
                            json.dumps(point["prerequisites"]),
                            point["importance"],
                            point["expected_mastery_level"],
                            point["confidence"],
                            json.dumps(point["sources"]),
                            now,
                            point_id,
                        ),
                    )
                else:
                    current = 0.0
                    conn.execute(
                        "INSERT INTO knowledge_points VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            point_id,
                            session["course_id"],
                            session_id,
                            point["title"],
                            point["description"],
                            json.dumps(point["prerequisites"]),
                            point["importance"],
                            point["expected_mastery_level"],
                            0.0,
                            point["confidence"],
                            json.dumps(point["sources"]),
                            now,
                            now,
                        ),
                    )
                title_to_id[point["title"]] = point_id
                estimated = max(3, point["importance"] * 3)
                conn.execute(
                    "INSERT INTO debts VALUES (?, ?, ?, ?, ?, 'unseen', ?, ?, 0, ?) ON CONFLICT(knowledge_point_id) DO UPDATE SET target_mastery=excluded.target_mastery, priority=excluded.priority, estimated_minutes=excluded.estimated_minutes, updated_at=excluded.updated_at",
                    (
                        _id(),
                        point_id,
                        session_id,
                        current,
                        point["expected_mastery_level"],
                        point["importance"],
                        estimated,
                        now,
                    ),
                )
            for step in payload["learning_path"]:
                point_ids = [title_to_id[t] for t in step["knowledge_point_titles"] if t in title_to_id]
                conn.execute(
                    "INSERT INTO learning_steps VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                    (
                        _id(),
                        session_id,
                        step["position"],
                        step["title"],
                        step["brief_explanation"],
                        step["full_explanation"],
                        json.dumps(point_ids),
                        step["estimated_minutes"],
                        step["confidence"],
                        json.dumps(step["sources"]),
                        now,
                        now,
                    ),
                )

    def get_reconstruction(self, session_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM reconstructions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def list_knowledge_points(self, session_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_points WHERE source_session_id=? ORDER BY importance DESC", (session_id,)
            ).fetchall()
        return [_decode(row) for row in rows]  # type: ignore[misc]

    def get_knowledge_point(self, point_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM knowledge_points WHERE id=?", (point_id,)).fetchone()
        if not row:
            raise KeyError("knowledge_point")
        return _decode(row)  # type: ignore[return-value]

    def save_remediation(self, point_id: str, reason: str, payload: dict[str, Any]) -> dict[str, Any]:
        now, row_id = utc_now(), _id()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO remediations VALUES (?, ?, ?, ?, ?)",
                (row_id, point_id, reason, json.dumps(payload, ensure_ascii=False), now),
            )
        return {
            "id": row_id,
            "knowledge_point_id": point_id,
            "reason": reason,
            "payload": payload,
            "created_at": now,
        }

    def list_debts(self, session_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT d.*, k.title, k.description, k.source_session_id FROM debts d JOIN knowledge_points k ON k.id=d.knowledge_point_id"
        args: tuple[Any, ...] = ()
        if session_id:
            query, args = query + " WHERE d.session_id=?", (session_id,)
        query += " ORDER BY d.status='mastered', d.priority DESC, d.updated_at DESC"
        with self.connect() as conn:
            rows = conn.execute(query, args).fetchall()
        return [_decode(row) for row in rows]  # type: ignore[misc]

    def list_learning_steps(self, session_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM learning_steps WHERE session_id=? ORDER BY position", (session_id,)
            ).fetchall()
        return [_decode(row) for row in rows]  # type: ignore[misc]

    def complete_learning_step(self, step_id: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE learning_steps SET completed=1, updated_at=? WHERE id=?", (utc_now(), step_id))

    def replace_questions(self, session_id: str, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        points = {p["title"]: p for p in self.list_knowledge_points(session_id)}
        now = utc_now()
        ids: list[str] = []
        with self.connect() as conn:
            conn.execute("UPDATE questions SET active=0 WHERE session_id=?", (session_id,))
            for question in questions:
                point = points.get(question["knowledge_point_title"])
                if not point:
                    continue
                question_id = _id()
                ids.append(question_id)
                conn.execute(
                    "INSERT INTO questions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                    (
                        question_id,
                        session_id,
                        point["id"],
                        question["prompt"],
                        question["level"],
                        question["expected_mastery_level"],
                        question["reference_answer"],
                        json.dumps(question["rubric"], ensure_ascii=False),
                        json.dumps(question["source_refs"]),
                        now,
                    ),
                )
        return [self.get_question(item) for item in ids]

    def list_questions(self, session_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM questions WHERE session_id=? AND active=1 ORDER BY created_at", (session_id,)
            ).fetchall()
        return [_decode(row) for row in rows]  # type: ignore[misc]

    def get_question(self, question_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone()
        if not row:
            raise KeyError("question")
        return _decode(row)  # type: ignore[return-value]

    def save_attempt_and_mastery(
        self, question_id: str, answer: str, evaluation: dict[str, Any], new_mastery: float, status: str
    ) -> dict[str, Any]:
        question = self.get_question(question_id)
        now, attempt_id = utc_now(), _id()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?)",
                (attempt_id, question_id, answer, json.dumps(evaluation, ensure_ascii=False), evaluation["score"], now),
            )
            conn.execute(
                "UPDATE knowledge_points SET current_mastery=?, updated_at=? WHERE id=?",
                (new_mastery, now, question["knowledge_point_id"]),
            )
            conn.execute(
                "UPDATE debts SET current_mastery=?, status=?, updated_at=? WHERE knowledge_point_id=?",
                (new_mastery, status, now, question["knowledge_point_id"]),
            )
        return {
            "id": attempt_id,
            "question_id": question_id,
            "answer": answer,
            "evaluation": evaluation,
            "score": evaluation["score"],
            "new_mastery": new_mastery,
            "debt_status": status,
            "created_at": now,
        }

    def update_session_scores(self, session_id: str, reconstruction: int, coverage: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE sessions SET reconstruction_score=?, learning_coverage=?, updated_at=? WHERE id=?",
                (reconstruction, coverage, utc_now(), session_id),
            )

    def refresh_session_status(self, session_id: str) -> str:
        debts = self.list_debts(session_id)
        status = "complete" if debts and all(d["status"] == "mastered" for d in debts) else "open"
        with self.connect() as conn:
            conn.execute("UPDATE sessions SET status=?, updated_at=? WHERE id=?", (status, utc_now(), session_id))
        return status
