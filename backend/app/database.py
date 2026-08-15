from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, text

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
  local_path TEXT, storage_provider TEXT NOT NULL DEFAULT 'local', storage_key TEXT,
  external_url TEXT, extracted_text TEXT NOT NULL DEFAULT '',
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
CREATE TABLE IF NOT EXISTS document_chunks (
  id TEXT PRIMARY KEY, resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  position INTEGER NOT NULL, text TEXT NOT NULL, locator_type TEXT NOT NULL,
  page INTEGER, slide INTEGER, content_kind TEXT NOT NULL DEFAULT 'text', visual_path TEXT,
  embedding_json TEXT, metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
  UNIQUE(resource_id, position)
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
  knowledge_point_ids_json TEXT NOT NULL DEFAULT '[]',
  prompt TEXT NOT NULL, level TEXT NOT NULL, expected_mastery INTEGER NOT NULL,
  question_type TEXT NOT NULL DEFAULT 'diagnostic', parent_question_id TEXT,
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
CREATE TABLE IF NOT EXISTS mastery_evidence (
  id TEXT PRIMARY KEY, knowledge_point_id TEXT NOT NULL REFERENCES knowledge_points(id) ON DELETE CASCADE,
  question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  attempt_id TEXT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
  evidence_type TEXT NOT NULL, score REAL NOT NULL, weight REAL NOT NULL DEFAULT 1,
  metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_point_dependencies (
  knowledge_point_id TEXT NOT NULL REFERENCES knowledge_points(id) ON DELETE CASCADE,
  prerequisite_id TEXT NOT NULL REFERENCES knowledge_points(id) ON DELETE CASCADE,
  relation_type TEXT NOT NULL DEFAULT 'prerequisite', created_at TEXT NOT NULL,
  PRIMARY KEY (knowledge_point_id, prerequisite_id)
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
  resource_id TEXT REFERENCES resources(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
  payload_json TEXT NOT NULL DEFAULT '{}', result_json TEXT, error TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_course ON sessions(course_id);
CREATE INDEX IF NOT EXISTS idx_resources_session ON resources(session_id);
CREATE INDEX IF NOT EXISTS idx_document_chunks_resource ON document_chunks(resource_id, position);
CREATE INDEX IF NOT EXISTS idx_debts_session ON debts(session_id);
CREATE INDEX IF NOT EXISTS idx_mastery_evidence_point ON mastery_evidence(knowledge_point_id, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_session ON jobs(session_id, created_at);
"""


def _id() -> str:
    return str(uuid.uuid4())


def _decode(row: sqlite3.Row | Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for key in tuple(result):
        if key.endswith("_json"):
            raw = result.pop(key)
            result[key.removesuffix("_json")] = json.loads(raw) if raw is not None else None
    for key in ("completed", "active", "blocks_next_session"):
        if key in result:
            result[key] = bool(result[key])
    return result


def _named_statement(statement: str, values: Sequence[Any]) -> tuple[str, dict[str, Any]]:
    """Translate the existing DB-API qmark syntax to SQLAlchemy named parameters."""

    pieces = statement.split("?")
    if len(pieces) - 1 != len(values):
        raise ValueError("SQL placeholder count does not match supplied values")
    rendered = pieces[0]
    params: dict[str, Any] = {}
    for index, value in enumerate(values):
        name = f"value_{index}"
        rendered += f":{name}{pieces[index + 1]}"
        params[name] = value
    return rendered, params


class _AlchemyResult:
    def __init__(self, result: Any):
        self._result = result

    def fetchone(self) -> Mapping[str, Any] | None:
        return self._result.mappings().fetchone()

    def fetchall(self) -> list[Mapping[str, Any]]:
        return list(self._result.mappings().fetchall())


class _AlchemyConnection:
    """Small compatibility adapter while data access moves incrementally to ORM repositories."""

    def __init__(self, connection: Connection):
        self._connection = connection

    def execute(self, statement: str, values: Sequence[Any] = ()) -> _AlchemyResult:
        rendered, params = _named_statement(statement, values)
        return _AlchemyResult(self._connection.execute(text(rendered), params))

    def executemany(self, statement: str, values: Sequence[Sequence[Any]]) -> None:
        rows = list(values)
        if not rows:
            return
        rendered, _ = _named_statement(statement, rows[0])
        params = [_named_statement(statement, row)[1] for row in rows]
        self._connection.execute(text(rendered), params)

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            statement = statement.strip()
            if statement and not statement.upper().startswith("PRAGMA"):
                self._connection.execute(text(statement))


class Database:
    def __init__(self, path: Path | str):
        self.path = path
        self.engine: Engine | None = None
        self.uses_sqlalchemy = isinstance(path, str) and "://" in path
        self.is_postgres = isinstance(path, str) and path.startswith(("postgres://", "postgresql"))
        if self.uses_sqlalchemy:
            database_url = str(path)
            if database_url.startswith("postgres://"):
                database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
            elif database_url.startswith("postgresql://"):
                database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
            self.engine = create_engine(database_url, pool_pre_ping=True)
        else:
            self.path = Path(path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            if not self.uses_sqlalchemy:
                self._migrate_legacy_schema(conn)

    @staticmethod
    def _migrate_legacy_schema(conn: sqlite3.Connection) -> None:
        resource_columns = {row["name"] for row in conn.execute("PRAGMA table_info(resources)")}
        resource_migrations = {
            "start_offset": "ALTER TABLE resources ADD COLUMN start_offset REAL",
            "end_offset": "ALTER TABLE resources ADD COLUMN end_offset REAL",
            "session_duration": "ALTER TABLE resources ADD COLUMN session_duration REAL",
            "capture_range_json": "ALTER TABLE resources ADD COLUMN capture_range_json TEXT NOT NULL DEFAULT '[]'",
            "storage_provider": "ALTER TABLE resources ADD COLUMN storage_provider TEXT NOT NULL DEFAULT 'local'",
            "storage_key": "ALTER TABLE resources ADD COLUMN storage_key TEXT",
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

        question_columns = {row["name"] for row in conn.execute("PRAGMA table_info(questions)")}
        question_migrations = {
            "knowledge_point_ids_json": "ALTER TABLE questions ADD COLUMN knowledge_point_ids_json TEXT NOT NULL DEFAULT '[]'",
            "question_type": "ALTER TABLE questions ADD COLUMN question_type TEXT NOT NULL DEFAULT 'diagnostic'",
            "parent_question_id": "ALTER TABLE questions ADD COLUMN parent_question_id TEXT",
        }
        for column, statement in question_migrations.items():
            if column not in question_columns:
                conn.execute(statement)
        conn.execute(
            "UPDATE questions SET knowledge_point_ids_json=json_array(knowledge_point_id) WHERE knowledge_point_ids_json='[]'"
        )

        for row in conn.execute("SELECT id, profile_json FROM courses").fetchall():
            profile = json.loads(row["profile_json"])
            if not set(DEFAULT_PROFILE).issubset(profile):
                conn.execute(
                    "UPDATE courses SET profile_json=?, updated_at=? WHERE id=?",
                    (json.dumps(DEFAULT_PROFILE), utc_now(), row["id"]),
                )

    @contextmanager
    def connect(self) -> Iterator[Any]:
        if self.engine:
            with self.engine.begin() as connection:
                yield _AlchemyConnection(connection)
            return
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
            "storage_provider": values.get("storage_provider", "local"),
            "storage_key": values.get("storage_key"),
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
        result["chunks"] = self.list_document_chunks(resource_id)
        return result  # type: ignore[return-value]

    def list_resources(self, session_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM resources WHERE session_id = ? ORDER BY created_at", (session_id,)
            ).fetchall()
        resources = [_decode(row) for row in rows]  # type: ignore[misc]
        for resource in resources:
            resource["transcript_segments"] = self.list_transcript_segments(resource["id"])
            resource["chunks"] = self.list_document_chunks(resource["id"])
        return resources

    def list_transcript_segments(self, resource_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM transcript_segments WHERE resource_id=? ORDER BY global_start, start_time",
                (resource_id,),
            ).fetchall()
        return [_decode(row) for row in rows]  # type: ignore[misc]

    def replace_document_chunks(self, resource_id: str, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.get_resource(resource_id)
        now = utc_now()
        with self.connect() as conn:
            conn.execute("DELETE FROM document_chunks WHERE resource_id=?", (resource_id,))
            conn.executemany(
                """INSERT INTO document_chunks
                   (id, resource_id, position, text, locator_type, page, slide, content_kind,
                    visual_path, embedding_json, metadata_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        chunk["id"],
                        resource_id,
                        chunk["position"],
                        chunk["text"],
                        chunk["locator_type"],
                        chunk.get("page"),
                        chunk.get("slide"),
                        chunk.get("content_kind", "text"),
                        chunk.get("visual_path"),
                        json.dumps(chunk.get("embedding")) if chunk.get("embedding") is not None else None,
                        json.dumps(chunk.get("metadata", {}), ensure_ascii=False),
                        now,
                    )
                    for chunk in chunks
                ],
            )
        return self.list_document_chunks(resource_id)

    def list_document_chunks(self, resource_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM document_chunks WHERE resource_id=? ORDER BY position", (resource_id,)
            ).fetchall()
        return [_decode(row) for row in rows]  # type: ignore[misc]

    def list_session_chunks(self, session_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT c.*, r.session_id, r.type AS resource_type, r.evidence_level, r.name AS resource_name
                   FROM document_chunks c JOIN resources r ON r.id=c.resource_id
                   WHERE r.session_id=? ORDER BY c.position""",
                (session_id,),
            ).fetchall()
        return [_decode(row) for row in rows]  # type: ignore[misc]

    def update_chunk_embeddings(self, embeddings: dict[str, list[float]]) -> None:
        with self.connect() as conn:
            conn.executemany(
                "UPDATE document_chunks SET embedding_json=? WHERE id=?",
                [(json.dumps(vector), chunk_id) for chunk_id, vector in embeddings.items()],
            )

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
            session_point_ids = tuple(title_to_id.values())
            if session_point_ids:
                placeholders = ", ".join("?" for _ in session_point_ids)
                conn.execute(
                    f"DELETE FROM knowledge_point_dependencies WHERE knowledge_point_id IN ({placeholders})",
                    session_point_ids,
                )
            course_points = {
                row["title"]: row["id"]
                for row in conn.execute(
                    "SELECT id, title FROM knowledge_points WHERE course_id=?", (session["course_id"],)
                ).fetchall()
            }
            for point in payload["knowledge_points"]:
                point_id = title_to_id[point["title"]]
                for prerequisite_title in point["prerequisites"]:
                    prerequisite_id = course_points.get(prerequisite_title)
                    if prerequisite_id and prerequisite_id != point_id:
                        conn.execute(
                            """INSERT INTO knowledge_point_dependencies
                               (knowledge_point_id, prerequisite_id, relation_type, created_at)
                               VALUES (?, ?, 'prerequisite', ?)
                               ON CONFLICT(knowledge_point_id, prerequisite_id) DO NOTHING""",
                            (point_id, prerequisite_id, now),
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
        self.refresh_dependency_flags(session["course_id"])

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
        return self._save_questions(session_id, questions, replace=True)

    def append_questions(
        self, session_id: str, questions: list[dict[str, Any]], parent_question_id: str | None = None
    ) -> list[dict[str, Any]]:
        return self._save_questions(session_id, questions, replace=False, parent_question_id=parent_question_id)

    def _save_questions(
        self,
        session_id: str,
        questions: list[dict[str, Any]],
        *,
        replace: bool,
        parent_question_id: str | None = None,
    ) -> list[dict[str, Any]]:
        points = {p["title"]: p for p in self.list_knowledge_points(session_id)}
        now = utc_now()
        ids: list[str] = []
        with self.connect() as conn:
            if replace:
                conn.execute("UPDATE questions SET active=0 WHERE session_id=?", (session_id,))
            for question in questions:
                titles = question.get("knowledge_point_titles") or [question.get("knowledge_point_title")]
                point_ids = [points[title]["id"] for title in titles if title in points]
                if not point_ids:
                    continue
                question_id = _id()
                ids.append(question_id)
                conn.execute(
                    """INSERT INTO questions
                       (id, session_id, knowledge_point_id, knowledge_point_ids_json, prompt, level,
                        expected_mastery, question_type, parent_question_id, reference_answer,
                        rubric_json, sources_json, active, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                    (
                        question_id,
                        session_id,
                        point_ids[0],
                        json.dumps(point_ids),
                        question["prompt"],
                        question["level"],
                        question["expected_mastery_level"],
                        "follow_up" if parent_question_id else question.get("question_type", "diagnostic"),
                        parent_question_id,
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

    def save_attempt(self, question_id: str, answer: str, evaluation: dict[str, Any]) -> dict[str, Any]:
        self.get_question(question_id)
        now, attempt_id = utc_now(), _id()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?)",
                (attempt_id, question_id, answer, json.dumps(evaluation, ensure_ascii=False), evaluation["score"], now),
            )
        return {
            "id": attempt_id,
            "question_id": question_id,
            "answer": answer,
            "evaluation": evaluation,
            "score": evaluation["score"],
            "created_at": now,
        }

    def add_mastery_evidence(
        self,
        attempt_id: str,
        question_id: str,
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        now = utc_now()
        evidence_ids: list[str] = []
        with self.connect() as conn:
            for result in results:
                evidence_id = _id()
                evidence_ids.append(evidence_id)
                conn.execute(
                    """INSERT INTO mastery_evidence
                       (id, knowledge_point_id, question_id, attempt_id, evidence_type, score, weight,
                        metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        evidence_id,
                        result["knowledge_point_id"],
                        question_id,
                        attempt_id,
                        result["evidence_type"],
                        result["score"],
                        result.get("weight", 1.0),
                        json.dumps(result.get("metadata", {}), ensure_ascii=False),
                        now,
                    ),
                )
        return [self.get_mastery_evidence(evidence_id) for evidence_id in evidence_ids]

    def get_mastery_evidence(self, evidence_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM mastery_evidence WHERE id=?", (evidence_id,)).fetchone()
        if not row:
            raise KeyError("mastery_evidence")
        return _decode(row)  # type: ignore[return-value]

    def list_mastery_evidence(self, point_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM mastery_evidence WHERE knowledge_point_id=? ORDER BY created_at", (point_id,)
            ).fetchall()
        return [_decode(row) for row in rows]  # type: ignore[misc]

    def update_point_mastery(self, point_id: str, mastery: float, status: str) -> None:
        point = self.get_knowledge_point(point_id)
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "UPDATE knowledge_points SET current_mastery=?, updated_at=? WHERE id=?",
                (mastery, now, point_id),
            )
            conn.execute(
                "UPDATE debts SET current_mastery=?, status=?, updated_at=? WHERE knowledge_point_id=?",
                (mastery, status, now, point_id),
            )
        self.refresh_dependency_flags(point["course_id"])

    def list_dependencies(self, course_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT dep.*, point.title AS knowledge_point_title,
                          prerequisite.title AS prerequisite_title
                   FROM knowledge_point_dependencies dep
                   JOIN knowledge_points point ON point.id=dep.knowledge_point_id
                   JOIN knowledge_points prerequisite ON prerequisite.id=dep.prerequisite_id
                   WHERE point.course_id=?""",
                (course_id,),
            ).fetchall()
        return [_decode(row) for row in rows]  # type: ignore[misc]

    def refresh_dependency_flags(self, course_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """UPDATE debts
                   SET blocks_next_session = CASE WHEN debts.status!='mastered' AND EXISTS (
                     SELECT 1 FROM knowledge_point_dependencies dep
                     JOIN debts dependent_debt ON dependent_debt.knowledge_point_id=dep.knowledge_point_id
                     JOIN knowledge_points dependent_point ON dependent_point.id=dep.knowledge_point_id
                     WHERE dep.prerequisite_id=debts.knowledge_point_id
                       AND dependent_point.course_id=?
                       AND dependent_debt.status!='mastered'
                   ) THEN 1 ELSE 0 END
                   WHERE knowledge_point_id IN (SELECT id FROM knowledge_points WHERE course_id=?)""",
                (course_id, course_id),
            )

    def create_job(
        self,
        kind: str,
        *,
        session_id: str | None = None,
        resource_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now, job_id = utc_now(), _id()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO jobs
                   (id, session_id, resource_id, kind, status, stage, progress, payload_json,
                    result_json, error, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'queued', 'queued', 0, ?, NULL, NULL, ?, ?)""",
                (job_id, session_id, resource_id, kind, json.dumps(payload or {}), now, now),
            )
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError("job")
        return _decode(row)  # type: ignore[return-value]

    def list_jobs(self, session_id: str | None = None) -> list[dict[str, Any]]:
        query, args = "SELECT * FROM jobs", ()
        if session_id:
            query, args = query + " WHERE session_id=?", (session_id,)
        with self.connect() as conn:
            rows = conn.execute(query + " ORDER BY created_at DESC", args).fetchall()
        return [_decode(row) for row in rows]  # type: ignore[misc]

    def update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        progress: int | None = None,
        result: Any = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_job(job_id)
        with self.connect() as conn:
            conn.execute(
                """UPDATE jobs SET status=?, stage=?, progress=?, result_json=?, error=?, updated_at=?
                   WHERE id=?""",
                (
                    status or current["status"],
                    stage or current["stage"],
                    progress if progress is not None else current["progress"],
                    json.dumps(result, ensure_ascii=False)
                    if result is not None
                    else (json.dumps(current["result"], ensure_ascii=False) if current.get("result") is not None else None),
                    error,
                    utc_now(),
                    job_id,
                ),
            )
        return self.get_job(job_id)

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
