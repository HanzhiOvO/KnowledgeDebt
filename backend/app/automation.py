from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, date, datetime
from typing import Any

from .database import Database, _decode
from .models import CourseCreate, SessionCreate, utc_now


def _id() -> str:
    return str(uuid.uuid4())


class AutomationRepository:
    """Persistence boundary for the v0.2 automation workbench."""

    def __init__(self, db: Database):
        self.db = db

    # Provider profiles -------------------------------------------------
    @staticmethod
    def _public_profile(row: Any) -> dict[str, Any]:
        profile = _decode(row)
        if not profile:
            raise KeyError("provider profile")
        profile.pop("credential_ciphertext", None)
        reference = profile.get("credential_reference") or ""
        _, separator, variable = reference.partition(":")
        reference_available = bool(separator and variable and os.getenv(variable))
        profile["credential_configured"] = bool(row["credential_ciphertext"]) or reference_available
        return profile

    def create_provider_profile(self, values: dict[str, Any]) -> dict[str, Any]:
        now, profile_id = utc_now(), _id()
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO provider_profiles
                   (id, name, vendor, adapter, base_url, region, credential_ciphertext,
                    credential_reference, default_model, capabilities_json, external, enabled,
                    implementation_status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    profile_id,
                    values["name"],
                    values["vendor"],
                    values["adapter"],
                    values.get("base_url", ""),
                    values.get("region"),
                    values.get("credential_ciphertext"),
                    values.get("credential_reference"),
                    values.get("default_model", ""),
                    json.dumps(values.get("capabilities", [])),
                    int(values.get("external", True)),
                    int(values.get("enabled", True)),
                    values.get("implementation_status", "available"),
                    now,
                    now,
                ),
            )
        return self.get_provider_profile(profile_id)

    def list_provider_profiles(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM provider_profiles ORDER BY created_at").fetchall()
        return [self._public_profile(row) for row in rows]

    def get_provider_profile(self, profile_id: str, *, include_secret: bool = False) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM provider_profiles WHERE id=?", (profile_id,)).fetchone()
        if not row:
            raise KeyError("provider profile")
        if include_secret:
            return _decode(row)  # type: ignore[return-value]
        return self._public_profile(row)

    def update_provider_profile(self, profile_id: str, values: dict[str, Any]) -> dict[str, Any]:
        self.get_provider_profile(profile_id)
        allowed = {
            "name",
            "base_url",
            "region",
            "credential_ciphertext",
            "credential_reference",
            "default_model",
            "external",
            "enabled",
        }
        updates: list[str] = []
        params: list[Any] = []
        for key, value in values.items():
            if key == "capabilities":
                updates.append("capabilities_json=?")
                params.append(json.dumps(value))
            elif key in allowed:
                updates.append(f"{key}=?")
                params.append(int(value) if key in {"external", "enabled"} else value)
        if updates:
            params.extend([utc_now(), profile_id])
            with self.db.connect() as conn:
                conn.execute(
                    f"UPDATE provider_profiles SET {', '.join(updates)}, updated_at=? WHERE id=?",
                    tuple(params),
                )
        return self.get_provider_profile(profile_id)

    def delete_provider_profile(self, profile_id: str) -> None:
        self.get_provider_profile(profile_id)
        with self.db.connect() as conn:
            conn.execute("DELETE FROM provider_profiles WHERE id=?", (profile_id,))

    def set_provider_default(self, group: str, profile_id: str) -> dict[str, Any]:
        profile = self.get_provider_profile(profile_id)
        if not profile["enabled"]:
            raise ValueError("disabled provider profile cannot be selected as a default")
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO provider_defaults (provider_group, profile_id, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(provider_group) DO UPDATE SET
                     profile_id=excluded.profile_id, updated_at=excluded.updated_at""",
                (group, profile_id, utc_now()),
            )
        return {"provider_group": group, "profile": profile}

    def get_provider_defaults(self) -> dict[str, dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM provider_defaults").fetchall()
        return {row["provider_group"]: self.get_provider_profile(row["profile_id"]) for row in rows}

    def update_provider_test(self, profile_id: str, status: str, message: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            conn.execute(
                """UPDATE provider_profiles SET last_test_status=?, last_test_message=?,
                   last_tested_at=?, updated_at=? WHERE id=?""",
                (status, message[:500], utc_now(), utc_now(), profile_id),
            )
        return self.get_provider_profile(profile_id)

    # Schedule ----------------------------------------------------------
    def create_term(self, values: dict[str, Any]) -> dict[str, Any]:
        if values["ends_on"] < values["starts_on"]:
            raise ValueError("term ends_on must not be before starts_on")
        now, term_id = utc_now(), _id()
        with self.db.connect() as conn:
            if values.get("current", True):
                conn.execute("UPDATE academic_terms SET current=0, updated_at=?", (now,))
            conn.execute(
                "INSERT INTO academic_terms VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    term_id,
                    values["name"],
                    values["starts_on"],
                    values["ends_on"],
                    values.get("timezone", "Asia/Shanghai"),
                    int(values.get("current", True)),
                    now,
                    now,
                ),
            )
        return self.get_term(term_id)

    def get_term(self, term_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM academic_terms WHERE id=?", (term_id,)).fetchone()
        if not row:
            raise KeyError("academic term")
        return _decode(row)  # type: ignore[return-value]

    def list_terms(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM academic_terms ORDER BY starts_on DESC").fetchall()
        return [_decode(row) for row in rows]  # type: ignore[misc]

    def upsert_schedule_connection(self, values: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.db.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM schedule_connections WHERE connector=?", (values["connector"],)
            ).fetchone()
            connection_id = existing["id"] if existing else _id()
            conn.execute(
                """INSERT INTO schedule_connections
                   (id, connector, display_name, sync_interval_minutes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(connector) DO UPDATE SET display_name=excluded.display_name,
                     sync_interval_minutes=excluded.sync_interval_minutes, updated_at=excluded.updated_at""",
                (
                    connection_id,
                    values["connector"],
                    values["display_name"],
                    values["sync_interval_minutes"],
                    now,
                    now,
                ),
            )
        return self.get_schedule_connection(values["connector"])

    def get_schedule_connection(self, connector: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM schedule_connections WHERE connector=?", (connector,)).fetchone()
        if not row:
            raise KeyError("schedule connection")
        result = _decode(row)  # type: ignore[assignment]
        result.pop("session_ciphertext", None)
        result["session_retained"] = bool(row["session_ciphertext"])
        return result

    def update_schedule_connection_state(
        self,
        connector: str,
        *,
        state: str,
        error: str | None = None,
        session_ciphertext: str | None = None,
        synced: bool = False,
        reauth_required: bool = False,
    ) -> dict[str, Any]:
        connection = self.get_schedule_connection(connector)
        with self.db.connect() as conn:
            conn.execute(
                """UPDATE schedule_connections SET state=?, last_error=?, session_ciphertext=COALESCE(?, session_ciphertext),
                   last_synced_at=CASE WHEN ?=1 THEN ? ELSE last_synced_at END,
                   reauth_required=?, updated_at=? WHERE id=?""",
                (
                    state,
                    error,
                    session_ciphertext,
                    int(synced),
                    utc_now(),
                    int(reauth_required),
                    utc_now(),
                    connection["id"],
                ),
            )
        return self.get_schedule_connection(connector)

    def upsert_schedule_rule(self, values: dict[str, Any]) -> dict[str, Any]:
        self.get_term(values["term_id"])
        now = utc_now()
        with self.db.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM schedule_rules WHERE term_id=? AND external_id=?",
                (values["term_id"], values["external_id"]),
            ).fetchone()
            rule_id = existing["id"] if existing else _id()
            conn.execute(
                """INSERT INTO schedule_rules
                   (id, term_id, course_id, course_name, course_code, class_name, teacher,
                    campus, building, room, weekday, start_period, end_period, weeks_json,
                    odd_even, notes, external_id, aliases_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(term_id, external_id) DO UPDATE SET
                     course_name=excluded.course_name, course_code=excluded.course_code,
                     class_name=excluded.class_name, teacher=excluded.teacher, campus=excluded.campus,
                     building=excluded.building, room=excluded.room, weekday=excluded.weekday,
                     start_period=excluded.start_period, end_period=excluded.end_period,
                     weeks_json=excluded.weeks_json, odd_even=excluded.odd_even, notes=excluded.notes,
                     aliases_json=excluded.aliases_json, updated_at=excluded.updated_at""",
                (
                    rule_id,
                    values["term_id"],
                    values.get("course_id"),
                    values["course_name"],
                    values.get("course_code"),
                    values.get("class_name"),
                    values.get("teacher"),
                    values.get("campus"),
                    values.get("building"),
                    values.get("room"),
                    values["weekday"],
                    values["start_period"],
                    values["end_period"],
                    json.dumps(sorted(set(values["weeks"]))),
                    values.get("odd_even", "all"),
                    values.get("notes", ""),
                    values["external_id"],
                    json.dumps(values.get("aliases", []), ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return self.get_schedule_rule(rule_id)

    def get_schedule_rule(self, rule_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM schedule_rules WHERE id=?", (rule_id,)).fetchone()
        if not row:
            raise KeyError("schedule rule")
        return _decode(row)  # type: ignore[return-value]

    def list_schedule_rules(self, term_id: str | None = None) -> list[dict[str, Any]]:
        query, args = "SELECT * FROM schedule_rules", ()
        if term_id:
            query, args = query + " WHERE term_id=?", (term_id,)
        with self.db.connect() as conn:
            rows = conn.execute(query + " ORDER BY weekday, start_period", args).fetchall()
        return [_decode(row) for row in rows]  # type: ignore[misc]

    def upsert_occurrence(self, values: dict[str, Any]) -> dict[str, Any]:
        rule = self.get_schedule_rule(values["rule_id"])
        now = utc_now()
        with self.db.connect() as conn:
            existing = conn.execute(
                """SELECT id FROM schedule_occurrences
                   WHERE rule_id=? AND occurrence_date=? AND starts_at=? AND ends_at=?""",
                (values["rule_id"], values["occurrence_date"], values["starts_at"], values["ends_at"]),
            ).fetchone()
            occurrence_id = existing["id"] if existing else _id()
            conn.execute(
                """INSERT INTO schedule_occurrences
                   (id, rule_id, course_id, occurrence_date, starts_at, ends_at, status, source_kind,
                    campus, building, room, teacher, notes, external_id, adjustment_of_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(rule_id, occurrence_date, starts_at, ends_at) DO UPDATE SET
                     course_id=excluded.course_id, status=excluded.status, source_kind=excluded.source_kind,
                     campus=excluded.campus, building=excluded.building, room=excluded.room,
                     teacher=excluded.teacher, notes=excluded.notes, external_id=excluded.external_id,
                     adjustment_of_id=excluded.adjustment_of_id, updated_at=excluded.updated_at""",
                (
                    occurrence_id,
                    values["rule_id"],
                    values.get("course_id") or rule.get("course_id"),
                    values["occurrence_date"],
                    values["starts_at"],
                    values["ends_at"],
                    values.get("status", "scheduled"),
                    values.get("source_kind", "regular"),
                    values.get("campus", rule.get("campus")),
                    values.get("building", rule.get("building")),
                    values.get("room", rule.get("room")),
                    values.get("teacher", rule.get("teacher")),
                    values.get("notes", rule.get("notes", "")),
                    values["external_id"],
                    values.get("adjustment_of_id"),
                    now,
                    now,
                ),
            )
        return self.get_occurrence(occurrence_id)

    def get_occurrence(self, occurrence_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM schedule_occurrences WHERE id=?", (occurrence_id,)).fetchone()
            session_row = conn.execute(
                "SELECT session_id FROM session_automation WHERE occurrence_id=?", (occurrence_id,)
            ).fetchone()
        if not row:
            raise KeyError("schedule occurrence")
        result = _decode(row)  # type: ignore[assignment]
        result["rule"] = self.get_schedule_rule(result["rule_id"])
        result["session_id"] = session_row["session_id"] if session_row else None
        return result

    def list_occurrences(self, starts_on: str | None = None, ends_on: str | None = None) -> list[dict[str, Any]]:
        filters: list[str] = []
        args: list[Any] = []
        if starts_on:
            filters.append("occurrence_date>=?")
            args.append(starts_on)
        if ends_on:
            filters.append("occurrence_date<=?")
            args.append(ends_on)
        query = "SELECT id FROM schedule_occurrences"
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY starts_at"
        with self.db.connect() as conn:
            rows = conn.execute(query, tuple(args)).fetchall()
        return [self.get_occurrence(row["id"]) for row in rows]

    def materialize_occurrence(self, occurrence_id: str, reason: str) -> dict[str, Any]:
        occurrence = self.get_occurrence(occurrence_id)
        if occurrence["session_id"]:
            return self.db.get_session(occurrence["session_id"])
        if occurrence["status"] == "cancelled":
            raise ValueError("cancelled occurrence cannot create a Session")
        rule = occurrence["rule"]
        course_id = occurrence.get("course_id") or rule.get("course_id")
        if not course_id:
            course = next(
                (item for item in self.db.list_courses() if item["name"] == rule["course_name"]),
                None,
            )
            if not course:
                course = self.db.create_course(
                    CourseCreate(
                        name=rule["course_name"],
                        semester=self.get_term(rule["term_id"])["name"],
                        teacher=rule.get("teacher"),
                    )
                )
            course_id = course["id"]
            with self.db.connect() as conn:
                conn.execute("UPDATE schedule_rules SET course_id=? WHERE id=?", (course_id, rule["id"]))
                conn.execute("UPDATE schedule_occurrences SET course_id=? WHERE id=?", (course_id, occurrence_id))
        title = f"{rule['course_name']}-{occurrence['occurrence_date']}-待识别"
        session = self.db.create_session(
            course_id,
            SessionCreate(
                title=title,
                starts_at=occurrence["starts_at"],
                ends_at=occurrence["ends_at"],
                notes=occurrence.get("notes", ""),
            ),
        )
        now = utc_now()
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO session_automation
                   (session_id, occurrence_id, materialization_reason, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (session["id"], occurrence_id, reason, now, now),
            )
        self.audit("materialize_occurrence", "session", session["id"], {"reason": reason})
        return self.db.get_session(session["id"])

    # Resources, transcription, inbox and review -----------------------
    def ensure_resource_automation(
        self, resource_id: str, *, state: str = "saved", auto_transcribe: bool = True
    ) -> dict[str, Any]:
        self.db.get_resource(resource_id)
        now = utc_now()
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO resource_automation
                   (resource_id, transcription_state, auto_transcribe, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(resource_id) DO UPDATE SET updated_at=excluded.updated_at""",
                (resource_id, state, int(auto_transcribe), now, now),
            )
        return self.get_resource_automation(resource_id)

    def get_resource_automation(self, resource_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM resource_automation WHERE resource_id=?", (resource_id,)).fetchone()
        if not row:
            return self.ensure_resource_automation(resource_id)
        return _decode(row)  # type: ignore[return-value]

    def update_resource_transcription(
        self,
        resource_id: str,
        state: str,
        *,
        error: str | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        self.ensure_resource_automation(resource_id)
        with self.db.connect() as conn:
            conn.execute(
                """UPDATE resource_automation SET transcription_state=?, failure_reason=?,
                   last_job_id=COALESCE(?, last_job_id), updated_at=? WHERE resource_id=?""",
                (state, error, job_id, utc_now(), resource_id),
            )
        return self.get_resource_automation(resource_id)

    def active_transcription_job(self, resource_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """SELECT * FROM jobs WHERE resource_id=? AND kind='transcription'
                   AND status IN ('queued', 'running') ORDER BY created_at DESC""",
                (resource_id,),
            ).fetchone()
        return _decode(row) if row else None

    def replace_transcription_chunks(self, resource_id: str, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = utc_now()
        with self.db.connect() as conn:
            existing = conn.execute(
                "SELECT position, status FROM transcription_chunks WHERE resource_id=?", (resource_id,)
            ).fetchall()
            successful = {row["position"] for row in existing if row["status"] == "succeeded"}
            for position, chunk in enumerate(chunks):
                if position in successful:
                    continue
                conn.execute(
                    """INSERT INTO transcription_chunks
                       (id, resource_id, position, start_seconds, end_seconds, media_path,
                        status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                       ON CONFLICT(resource_id, position) DO UPDATE SET
                         start_seconds=excluded.start_seconds, end_seconds=excluded.end_seconds,
                         media_path=excluded.media_path, updated_at=excluded.updated_at""",
                    (
                        _id(),
                        resource_id,
                        position,
                        chunk["start_seconds"],
                        chunk["end_seconds"],
                        chunk.get("media_path"),
                        now,
                        now,
                    ),
                )
        return self.list_transcription_chunks(resource_id)

    def list_transcription_chunks(self, resource_id: str) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM transcription_chunks WHERE resource_id=? ORDER BY position", (resource_id,)
            ).fetchall()
        return [_decode(row) for row in rows]  # type: ignore[misc]

    def update_transcription_chunk(
        self,
        chunk_id: str,
        status: str,
        *,
        error: str | None = None,
        segment_count: int | None = None,
        segments: list[dict[str, Any]] | None = None,
        increment_attempt: bool = False,
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            conn.execute(
                """UPDATE transcription_chunks SET status=?, error=?,
                   segment_count=COALESCE(?, segment_count),
                   segments_json=COALESCE(?, segments_json),
                   attempt_count=attempt_count+?, updated_at=? WHERE id=?""",
                (
                    status,
                    error,
                    segment_count,
                    json.dumps(segments, ensure_ascii=False) if segments is not None else None,
                    int(increment_attempt),
                    utc_now(),
                    chunk_id,
                ),
            )
            row = conn.execute("SELECT * FROM transcription_chunks WHERE id=?", (chunk_id,)).fetchone()
        if not row:
            raise KeyError("transcription chunk")
        return _decode(row)  # type: ignore[return-value]

    def create_inbox_item(self, values: dict[str, Any]) -> dict[str, Any]:
        now, item_id = utc_now(), _id()
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO inbox_items
                   (id, name, mime_type, type, storage_provider, storage_key, local_path,
                    captured_at, original_file_time, extracted_text, source, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item_id,
                    values["name"],
                    values.get("mime_type"),
                    values["type"],
                    values["storage_provider"],
                    values["storage_key"],
                    values.get("local_path"),
                    values.get("captured_at", now),
                    values.get("original_file_time"),
                    values.get("extracted_text", ""),
                    values.get("source", "global_upload"),
                    now,
                    now,
                ),
            )
        return self.get_inbox_item(item_id)

    def get_inbox_item(self, item_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM inbox_items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise KeyError("inbox item")
        return _decode(row)  # type: ignore[return-value]

    def list_inbox_items(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM inbox_items ORDER BY captured_at DESC").fetchall()
        return [_decode(row) for row in rows]  # type: ignore[misc]

    def unarchive_inbox_item(self, item_id: str) -> dict[str, Any]:
        self.get_inbox_item(item_id)
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE inbox_items SET archived=0, matching_status='pending', updated_at=? WHERE id=?",
                (utc_now(), item_id),
            )
        self.audit("unarchive_inbox_item", "inbox_item", item_id, {})
        return self.get_inbox_item(item_id)

    def update_inbox_match(
        self,
        item_id: str,
        *,
        status: str,
        confidence: float,
        reasons: list[str],
        suggested_session_id: str | None,
    ) -> dict[str, Any]:
        self.get_inbox_item(item_id)
        with self.db.connect() as conn:
            conn.execute(
                """UPDATE inbox_items SET matching_status=?, match_confidence=?, match_reasons_json=?,
                   suggested_session_id=?, updated_at=? WHERE id=?""",
                (status, confidence, json.dumps(reasons, ensure_ascii=False), suggested_session_id, utc_now(), item_id),
            )
        return self.get_inbox_item(item_id)

    def adopt_inbox_item(self, item_id: str, session_id: str, *, lock: bool = True) -> dict[str, Any]:
        item = self.get_inbox_item(item_id)
        if item.get("adopted_resource_id"):
            return self.db.get_resource(item["adopted_resource_id"])
        resource = self.db.add_resource(
            session_id,
            type=item["type"],
            evidence_level="classroom",
            name=item["name"],
            mime_type=item.get("mime_type"),
            local_path=item.get("local_path"),
            storage_provider=item["storage_provider"],
            storage_key=item["storage_key"],
            extracted_text=item.get("extracted_text", ""),
        )
        with self.db.connect() as conn:
            conn.execute(
                """UPDATE inbox_items SET matching_status='accepted', adopted_resource_id=?,
                   suggested_session_id=?, locked=?, archived=1, updated_at=? WHERE id=?""",
                (resource["id"], session_id, int(lock), utc_now(), item_id),
            )
        self.audit("adopt_inbox_item", "inbox_item", item_id, {"session_id": session_id})
        return resource

    def create_review_item(
        self,
        kind: str,
        subject_type: str,
        subject_id: str,
        title: str,
        *,
        proposed_value: str | None = None,
        confidence: float = 0,
        reasons: list[str] | None = None,
        navigation_path: str | None = None,
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            existing = conn.execute(
                """SELECT * FROM review_items WHERE kind=? AND subject_type=? AND subject_id=?
                   AND status='pending' ORDER BY created_at DESC""",
                (kind, subject_type, subject_id),
            ).fetchone()
            if existing:
                return _decode(existing)  # type: ignore[return-value]
            now, review_id = utc_now(), _id()
            conn.execute(
                """INSERT INTO review_items
                   (id, kind, subject_type, subject_id, title, proposed_value, confidence,
                    reasons_json, navigation_path, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    review_id,
                    kind,
                    subject_type,
                    subject_id,
                    title,
                    proposed_value,
                    confidence,
                    json.dumps(reasons or [], ensure_ascii=False),
                    navigation_path,
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM review_items WHERE id=?", (review_id,)).fetchone()
        return _decode(row)  # type: ignore[return-value]

    def list_review_items(self, status: str = "pending") -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM review_items WHERE status=? ORDER BY created_at", (status,)
            ).fetchall()
        return [_decode(row) for row in rows]  # type: ignore[misc]

    def get_review_item(self, review_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM review_items WHERE id=?", (review_id,)).fetchone()
        if not row:
            raise KeyError("review item")
        return _decode(row)  # type: ignore[return-value]

    def decide_review(self, review_id: str, action: str, reason: str = "") -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM review_items WHERE id=?", (review_id,)).fetchone()
            if not row:
                raise KeyError("review item")
            current = _decode(row)
            if current["status"] != "pending":
                return current  # type: ignore[return-value]
            status = {"accept": "accepted", "edit_accept": "accepted", "reject": "rejected", "later": "later"}[
                action
            ]
            conn.execute(
                """UPDATE review_items SET status=?, decision_reason=?, decided_at=?, updated_at=? WHERE id=?""",
                (status, reason, utc_now(), utc_now(), review_id),
            )
        self.audit("review_decision", "review_item", review_id, {"action": action, "reason": reason})
        with self.db.connect() as conn:
            updated = conn.execute("SELECT * FROM review_items WHERE id=?", (review_id,)).fetchone()
        return _decode(updated)  # type: ignore[return-value]

    def session_automation(self, session_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM session_automation WHERE session_id=?", (session_id,)).fetchone()
        if row:
            return _decode(row)  # type: ignore[return-value]
        now = utc_now()
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO session_automation (session_id, created_at, updated_at) VALUES (?, ?, ?)",
                (session_id, now, now),
            )
        return self.session_automation(session_id)

    def update_session_title(
        self,
        session_id: str,
        title: str,
        *,
        source: str,
        confidence: float,
        locked: bool | None = None,
    ) -> dict[str, Any]:
        automation = self.session_automation(session_id)
        if automation["title_locked"] and locked is not True:
            return self.db.get_session(session_id)
        with self.db.connect() as conn:
            conn.execute("UPDATE sessions SET title=?, updated_at=? WHERE id=?", (title, utc_now(), session_id))
            conn.execute(
                """UPDATE session_automation SET title_source=?, title_confidence=?,
                   title_locked=COALESCE(?, title_locked), topic_candidate=?, updated_at=? WHERE session_id=?""",
                (source, confidence, int(locked) if locked is not None else None, title, utc_now(), session_id),
            )
        self.audit("update_session_title", "session", session_id, {"source": source, "confidence": confidence})
        return self.db.get_session(session_id)

    # Audit and call ledger --------------------------------------------
    def audit(self, action: str, subject_type: str, subject_id: str, payload: dict[str, Any]) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO audit_log VALUES (?, ?, ?, ?, ?, ?)",
                (_id(), action, subject_type, subject_id, json.dumps(payload, ensure_ascii=False), utc_now()),
            )

    def log_provider_call(self, values: dict[str, Any]) -> dict[str, Any]:
        log_id = _id()
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO provider_call_logs
                   (id, operation, provider_profile_id, provider_name, model, job_id, resource_id,
                    session_id, status, duration_ms, request_count, audio_minutes, input_tokens,
                    output_tokens, estimated_cost, cost_currency, cost_known, error_type, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    log_id,
                    values["operation"],
                    values.get("provider_profile_id"),
                    values["provider_name"],
                    values.get("model"),
                    values.get("job_id"),
                    values.get("resource_id"),
                    values.get("session_id"),
                    values["status"],
                    values.get("duration_ms", 0),
                    values.get("request_count", 1),
                    values.get("audio_minutes"),
                    values.get("input_tokens"),
                    values.get("output_tokens"),
                    values.get("estimated_cost"),
                    values.get("cost_currency"),
                    int(values.get("cost_known", False)),
                    values.get("error_type"),
                    utc_now(),
                ),
            )
            row = conn.execute("SELECT * FROM provider_call_logs WHERE id=?", (log_id,)).fetchone()
        return _decode(row)  # type: ignore[return-value]

    def provider_usage(self, month: str | None = None) -> dict[str, Any]:
        month = month or date.today().isoformat()[:7]
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM provider_call_logs WHERE created_at LIKE ? ORDER BY created_at DESC",
                (f"{month}%",),
            ).fetchall()
        items = [_decode(row) for row in rows]
        return {
            "month": month,
            "request_count": sum(item["request_count"] for item in items),
            "transcription_minutes": round(sum(item.get("audio_minutes") or 0 for item in items), 2),
            "known_cost": round(sum(item.get("estimated_cost") or 0 for item in items if item["cost_known"]), 6),
            "unknown_cost_count": sum(not item["cost_known"] for item in items),
            "failure_count": sum(item["status"] == "failed" for item in items),
            "items": items,
        }


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
