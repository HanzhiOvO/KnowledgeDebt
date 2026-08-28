from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo

from .database import Database, _decode
from .models import DEFAULT_PROFILE, utc_now


def _id() -> str:
    return str(uuid.uuid4())


class AutomationRepository:
    """Persistence boundary for the v0.2 automation workbench."""

    def __init__(self, db: Database):
        self.db = db

    # Application settings --------------------------------------------
    def get_app_settings(
        self,
        *,
        default_timezone: str = "Asia/Shanghai",
        default_auto_transcribe: bool = True,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO app_settings
                   (id, timezone, auto_transcribe, recording_chunk_retention_days,
                    created_at, updated_at)
                   VALUES ('default', ?, ?, 14, ?, ?)
                   ON CONFLICT(id) DO NOTHING""",
                (default_timezone, int(default_auto_transcribe), now, now),
            )
            row = conn.execute("SELECT * FROM app_settings WHERE id='default'").fetchone()
        return _decode(row)  # type: ignore[return-value]

    def update_app_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        current = self.get_app_settings()
        timezone = values.get("timezone", current["timezone"])
        auto_transcribe = values.get("auto_transcribe", current["auto_transcribe"])
        retention = (
            values["recording_chunk_retention_days"]
            if "recording_chunk_retention_days" in values
            else current["recording_chunk_retention_days"]
        )
        with self.db.connect() as conn:
            conn.execute(
                """UPDATE app_settings SET timezone=?, auto_transcribe=?,
                   recording_chunk_retention_days=?, updated_at=?
                   WHERE id='default'""",
                (timezone, int(auto_transcribe), retention, utc_now()),
            )
        updated = self.get_app_settings()
        self.audit(
            "update_app_settings",
            "app_settings",
            "default",
            {
                "timezone": updated["timezone"],
                "auto_transcribe": updated["auto_transcribe"],
                "recording_chunk_retention_days": updated["recording_chunk_retention_days"],
            },
        )
        return updated

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
                    credential_reference, default_model, capabilities_json, custom_headers_json,
                    external, enabled,
                    implementation_status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    json.dumps(values.get("custom_headers", {}), ensure_ascii=False),
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
            "vendor",
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
            elif key == "custom_headers":
                updates.append("custom_headers_json=?")
                params.append(json.dumps(value, ensure_ascii=False))
            elif key in allowed:
                updates.append(f"{key}=?")
                params.append(int(value) if key in {"external", "enabled"} else value)
        test_relevant = {
            "vendor",
            "base_url",
            "credential_ciphertext",
            "credential_reference",
            "default_model",
            "capabilities",
            "custom_headers",
            "external",
            "enabled",
        }
        if test_relevant.intersection(values):
            updates.extend(
                [
                    "last_test_status=NULL",
                    "last_test_message=NULL",
                    "last_tested_at=NULL",
                ]
            )
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
            raise ValueError("已禁用的 Provider Profile 不能设为默认路由。")
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
            raise ValueError("学期结束日期不能早于开始日期。")
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

    def create_schedule_sync_batch(
        self,
        connector: str,
        source: str,
        parsed: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Persist a normalized authoritative snapshot and its reviewable diff."""

        term_data = parsed["term"]
        academic_term = "|".join(
            (term_data["name"], term_data["starts_on"], term_data["ends_on"])
        )
        canonical = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        snapshot_hash = sha256(canonical.encode("utf-8")).hexdigest()
        rule_ids = {rule["external_id"] for rule in parsed["rules"]}
        unknown = sorted(
            {
                occurrence["rule_external_id"]
                for occurrence in parsed["occurrences"]
                if occurrence["rule_external_id"] not in rule_ids
            }
        )
        if unknown:
            raise ValueError(f"课表实例引用了未知课程规则：{', '.join(unknown)}")

        term = next(
            (
                item
                for item in self.list_terms()
                if item["name"] == term_data["name"]
                and item["starts_on"] == term_data["starts_on"]
                and item["ends_on"] == term_data["ends_on"]
            ),
            None,
        )
        existing: dict[str, dict[str, Any]] = {}
        legacy_adoption: dict[str, list[str]] = {"occurrence_ids": [], "rule_ids": []}
        if term:
            with self.db.connect() as conn:
                rows = conn.execute(
                    """SELECT o.*, r.external_id AS rule_external_id, r.course_name,
                              parent.external_id AS adjustment_of_external_id, sa.session_id
                       FROM schedule_occurrences o
                       JOIN schedule_rules r ON r.id=o.rule_id
                       LEFT JOIN schedule_occurrences parent ON parent.id=o.adjustment_of_id
                       LEFT JOIN session_automation sa ON sa.occurrence_id=o.id
                       WHERE r.term_id=? AND o.source=? AND o.sync_status='active'""",
                    (term["id"], source),
                ).fetchall()
                prior_applied = conn.execute(
                    """SELECT 1 FROM schedule_sync_batches
                       WHERE connector=? AND source=? AND academic_term=? AND status='applied'
                       LIMIT 1""",
                    (connector, source, academic_term),
                ).fetchone()
                legacy_rows = []
                if source == "zjsu_fixture" and not prior_applied:
                    # Before v0.2, fixture imports were indistinguishable from manual rows.
                    # Only adopt the stable regular-occurrence shape produced by that parser,
                    # and only on the first confirmed authoritative snapshot for this term.
                    legacy_rows = conn.execute(
                        """SELECT o.*, r.external_id AS rule_external_id, r.course_name,
                                  parent.external_id AS adjustment_of_external_id, sa.session_id
                           FROM schedule_occurrences o
                           JOIN schedule_rules r ON r.id=o.rule_id
                           LEFT JOIN schedule_occurrences parent ON parent.id=o.adjustment_of_id
                           LEFT JOIN session_automation sa ON sa.occurrence_id=o.id
                           WHERE r.term_id=? AND r.source='manual' AND o.source='manual'
                             AND o.sync_status='active' AND o.source_kind='regular'
                             AND o.external_id=(r.external_id || ':' || o.occurrence_date)""",
                        (term["id"],),
                    ).fetchall()
            existing = {row["external_id"]: _decode(row) for row in rows}  # type: ignore[misc]
            for row in legacy_rows:
                decoded = _decode(row)
                existing.setdefault(row["external_id"], decoded)  # type: ignore[arg-type]
                legacy_adoption["occurrence_ids"].append(row["id"])
                legacy_adoption["rule_ids"].append(row["rule_id"])
            legacy_adoption["rule_ids"] = sorted(set(legacy_adoption["rule_ids"]))
        rules_by_external = {rule["external_id"]: rule for rule in parsed["rules"]}
        incoming = {item["external_id"]: item for item in parsed["occurrences"]}
        compare_fields = (
            "occurrence_date",
            "starts_at",
            "ends_at",
            "status",
            "source_kind",
            "campus",
            "building",
            "room",
            "teacher",
            "notes",
            "adjustment_external_id",
            "adjustment_of_external_id",
            "rule_external_id",
        )
        added: list[dict[str, Any]] = []
        modified: list[dict[str, Any]] = []
        removed: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        if legacy_adoption["occurrence_ids"]:
            conflicts.append(
                {
                    "external_id": "legacy-zjsu-fixture-adoption",
                    "course_name": "旧版课表数据归属",
                    "occurrence_date": term_data["starts_on"],
                    "starts_at": "",
                    "ends_at": "",
                    "reason": (
                        f"检测到 {len(legacy_adoption['occurrence_ids'])} 个旧版课表实例未记录来源。"
                        "确认应用后会把这些规则纳入本次浙江工商大学权威快照；"
                        "新快照缺少的未来安排将标记为已移除，历史 Session 和资料仍会保留。"
                    ),
                }
            )
        for external_id, occurrence in incoming.items():
            rule = rules_by_external[occurrence["rule_external_id"]]
            summary = {
                "external_id": external_id,
                "course_name": rule["course_name"],
                "occurrence_date": occurrence["occurrence_date"],
                "starts_at": occurrence["starts_at"],
                "ends_at": occurrence["ends_at"],
            }
            previous = existing.get(external_id)
            if not previous:
                added.append(summary)
                continue
            changes = {
                field: {"before": previous.get(field), "after": occurrence.get(field)}
                for field in compare_fields
                if previous.get(field) != occurrence.get(field)
            }
            rule_changes = {
                field: {"before": previous.get(field), "after": rule.get(field)}
                for field in ("course_name",)
                if previous.get(field) != rule.get(field)
            }
            if changes or rule_changes:
                item = {**summary, "changes": {**changes, **rule_changes}}
                modified.append(item)
                if previous.get("session_id"):
                    conflicts.append(
                        {
                            **item,
                            "reason": "该课堂已经产生 Session；历史内容会保留，请确认新课表时间。",
                            "session_id": previous["session_id"],
                        }
                    )
        timezone = ZoneInfo(term_data.get("timezone", "Asia/Shanghai"))
        local_today = (now or datetime.now(UTC)).astimezone(timezone).date().isoformat()
        for external_id, previous in existing.items():
            if external_id in incoming or previous["occurrence_date"] < local_today:
                continue
            item = {
                "external_id": external_id,
                "course_name": previous["course_name"],
                "occurrence_date": previous["occurrence_date"],
                "starts_at": previous["starts_at"],
                "ends_at": previous["ends_at"],
            }
            removed.append(item)
            if previous.get("session_id"):
                conflicts.append(
                    {
                        **item,
                        "reason": "该课堂已有关联 Session；只停用未来安排，不删除历史资料。",
                        "session_id": previous["session_id"],
                    }
                )
        diff = {
            "added": added,
            "modified": modified,
            "removed": removed,
            "conflicts": conflicts,
            "legacy_adoption": legacy_adoption,
            "summary": {
                "added": len(added),
                "modified": len(modified),
                "removed": len(removed),
                "conflicts": len(conflicts),
            },
        }
        batch_id, created_at = _id(), utc_now()
        with self.db.connect(immediate=True) as conn:
            conn.execute(
                """INSERT INTO schedule_sync_batches
                   (id, connector, source, academic_term, term_id, snapshot_hash, status,
                    payload_json, diff_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                   ON CONFLICT(connector, academic_term, snapshot_hash) DO NOTHING""",
                (
                    batch_id,
                    connector,
                    source,
                    academic_term,
                    term["id"] if term else None,
                    snapshot_hash,
                    canonical,
                    json.dumps(diff, ensure_ascii=False),
                    created_at,
                ),
            )
            row = conn.execute(
                """SELECT * FROM schedule_sync_batches
                   WHERE connector=? AND academic_term=? AND snapshot_hash=?""",
                (connector, academic_term, snapshot_hash),
            ).fetchone()
        return _decode(row)  # type: ignore[return-value]

    def get_schedule_sync_batch(self, batch_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM schedule_sync_batches WHERE id=?", (batch_id,)
            ).fetchone()
        if not row:
            raise KeyError("schedule sync batch")
        return _decode(row)  # type: ignore[return-value]

    def apply_schedule_sync_batch(
        self,
        batch_id: str,
        *,
        now: datetime | None = None,
        fail_after: int | None = None,
    ) -> dict[str, Any]:
        """Apply one snapshot atomically; fail_after exists only for transaction tests."""

        batch = self.get_schedule_sync_batch(batch_id)
        if batch["status"] == "applied":
            return batch
        parsed = batch["payload"]
        term_data = parsed["term"]
        changed_rows = 0
        try:
            with self.db.connect(immediate=True) as conn:
                conn.execute(
                    "UPDATE schedule_sync_batches SET status=status WHERE id=?", (batch_id,)
                )
                locked = conn.execute(
                    "SELECT * FROM schedule_sync_batches WHERE id=?", (batch_id,)
                ).fetchone()
                if not locked:
                    raise KeyError("schedule sync batch")
                if locked["status"] == "applied":
                    return self.get_schedule_sync_batch(batch_id)
                term = conn.execute(
                    """SELECT * FROM academic_terms
                       WHERE name=? AND starts_on=? AND ends_on=?""",
                    (term_data["name"], term_data["starts_on"], term_data["ends_on"]),
                ).fetchone()
                term_id = term["id"] if term else _id()
                timestamp = utc_now()
                if not term:
                    if term_data.get("current", True):
                        conn.execute("UPDATE academic_terms SET current=0, updated_at=?", (timestamp,))
                    conn.execute(
                        """INSERT INTO academic_terms
                           (id, name, starts_on, ends_on, timezone, current, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            term_id,
                            term_data["name"],
                            term_data["starts_on"],
                            term_data["ends_on"],
                            term_data.get("timezone", "Asia/Shanghai"),
                            int(term_data.get("current", True)),
                            timestamp,
                            timestamp,
                        ),
                    )
                legacy_adoption = batch["diff"].get("legacy_adoption", {})
                legacy_occurrence_ids = legacy_adoption.get("occurrence_ids", [])
                legacy_rule_ids = legacy_adoption.get("rule_ids", [])
                if legacy_occurrence_ids:
                    placeholders = ",".join("?" for _ in legacy_occurrence_ids)
                    conn.execute(
                        f"""UPDATE schedule_occurrences
                            SET source=?, updated_at=?
                            WHERE source='manual' AND id IN ({placeholders})""",
                        (batch["source"], timestamp, *legacy_occurrence_ids),
                    )
                if legacy_rule_ids:
                    placeholders = ",".join("?" for _ in legacy_rule_ids)
                    conn.execute(
                        f"""UPDATE schedule_rules
                            SET source=?, updated_at=?
                            WHERE source='manual' AND id IN ({placeholders})""",
                        (batch["source"], timestamp, *legacy_rule_ids),
                    )
                rules: dict[str, str] = {}
                for rule in parsed["rules"]:
                    existing_rule = conn.execute(
                        "SELECT id FROM schedule_rules WHERE term_id=? AND external_id=?",
                        (term_id, rule["external_id"]),
                    ).fetchone()
                    rule_id = existing_rule["id"] if existing_rule else _id()
                    conn.execute(
                        """INSERT INTO schedule_rules
                           (id, term_id, course_name, course_code, class_name, teacher, campus,
                            building, room, weekday, start_period, end_period, weeks_json,
                            odd_even, notes, external_id, aliases_json, source, last_seen_batch_id,
                            sync_status, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                   'active', ?, ?)
                           ON CONFLICT(term_id, external_id) DO UPDATE SET
                             course_name=excluded.course_name, course_code=excluded.course_code,
                             class_name=excluded.class_name, teacher=excluded.teacher,
                             campus=excluded.campus, building=excluded.building, room=excluded.room,
                             weekday=excluded.weekday, start_period=excluded.start_period,
                             end_period=excluded.end_period, weeks_json=excluded.weeks_json,
                             odd_even=excluded.odd_even, notes=excluded.notes,
                             aliases_json=excluded.aliases_json, source=excluded.source,
                             last_seen_batch_id=excluded.last_seen_batch_id, sync_status='active',
                             updated_at=excluded.updated_at""",
                        (
                            rule_id,
                            term_id,
                            rule["course_name"],
                            rule.get("course_code"),
                            rule.get("class_name"),
                            rule.get("teacher"),
                            rule.get("campus"),
                            rule.get("building"),
                            rule.get("room"),
                            rule["weekday"],
                            rule["start_period"],
                            rule["end_period"],
                            json.dumps(rule["weeks"]),
                            rule.get("odd_even", "all"),
                            rule.get("notes", ""),
                            rule["external_id"],
                            json.dumps(rule.get("aliases", []), ensure_ascii=False),
                            batch["source"],
                            batch_id,
                            timestamp,
                            timestamp,
                        ),
                    )
                    rules[rule["external_id"]] = rule_id
                    changed_rows += 1
                    if fail_after is not None and changed_rows >= fail_after:
                        raise RuntimeError("simulated schedule sync interruption")
                occurrence_ids: dict[str, str] = {}
                for occurrence in parsed["occurrences"]:
                    rule_id = rules[occurrence["rule_external_id"]]
                    existing_occurrence = conn.execute(
                        """SELECT id FROM schedule_occurrences
                           WHERE rule_id=? AND external_id=?""",
                        (rule_id, occurrence["external_id"]),
                    ).fetchone()
                    occurrence_ids[occurrence["external_id"]] = (
                        existing_occurrence["id"] if existing_occurrence else _id()
                    )
                for occurrence in parsed["occurrences"]:
                    rule_id = rules[occurrence["rule_external_id"]]
                    occurrence_id = occurrence_ids[occurrence["external_id"]]
                    adjustment_of_external_id = occurrence.get("adjustment_of_external_id")
                    adjustment_of_id = (
                        occurrence_ids.get(adjustment_of_external_id)
                        if adjustment_of_external_id
                        else None
                    )
                    if adjustment_of_external_id and not adjustment_of_id:
                        raise ValueError(
                            f"调课实例引用了未知原课次：{adjustment_of_external_id}"
                        )
                    conn.execute(
                        """INSERT INTO schedule_occurrences
                           (id, rule_id, occurrence_date, starts_at, ends_at, status, source_kind,
                            campus, building, room, teacher, notes, external_id,
                            adjustment_external_id, adjustment_of_id, source, last_seen_batch_id,
                            sync_status, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                   'active', ?, ?)
                           ON CONFLICT(rule_id, external_id) DO UPDATE SET
                             occurrence_date=excluded.occurrence_date, starts_at=excluded.starts_at,
                             ends_at=excluded.ends_at, status=excluded.status,
                             source_kind=excluded.source_kind, campus=excluded.campus,
                             building=excluded.building, room=excluded.room,
                             teacher=excluded.teacher, notes=excluded.notes,
                             adjustment_external_id=excluded.adjustment_external_id,
                             adjustment_of_id=excluded.adjustment_of_id,
                             source=excluded.source, last_seen_batch_id=excluded.last_seen_batch_id,
                             sync_status='active', updated_at=excluded.updated_at""",
                        (
                            occurrence_id,
                            rule_id,
                            occurrence["occurrence_date"],
                            occurrence["starts_at"],
                            occurrence["ends_at"],
                            occurrence.get("status", "scheduled"),
                            occurrence.get("source_kind", "regular"),
                            occurrence.get("campus"),
                            occurrence.get("building"),
                            occurrence.get("room"),
                            occurrence.get("teacher"),
                            occurrence.get("notes", ""),
                            occurrence["external_id"],
                            occurrence.get("adjustment_external_id"),
                            adjustment_of_id,
                            batch["source"],
                            batch_id,
                            timestamp,
                            timestamp,
                        ),
                    )
                    changed_rows += 1
                    if fail_after is not None and changed_rows >= fail_after:
                        raise RuntimeError("simulated schedule sync interruption")
                timezone = ZoneInfo(term_data.get("timezone", "Asia/Shanghai"))
                local_today = (now or datetime.now(UTC)).astimezone(timezone).date().isoformat()
                conn.execute(
                    """UPDATE schedule_occurrences SET sync_status='removed', updated_at=?
                       WHERE source=? AND occurrence_date>=?
                         AND (last_seen_batch_id IS NULL OR last_seen_batch_id<>?)
                         AND rule_id IN (SELECT id FROM schedule_rules WHERE term_id=?)""",
                    (timestamp, batch["source"], local_today, batch_id, term_id),
                )
                conn.execute(
                    """UPDATE schedule_rules SET sync_status='removed', updated_at=?
                       WHERE term_id=? AND source=?
                         AND (last_seen_batch_id IS NULL OR last_seen_batch_id<>?)""",
                    (timestamp, term_id, batch["source"], batch_id),
                )
                conn.execute(
                    """UPDATE schedule_sync_batches SET status='applied', term_id=?, error=NULL,
                       applied_at=? WHERE id=?""",
                    (term_id, timestamp, batch_id),
                )
        except Exception as exc:
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE schedule_sync_batches SET status='failed', error=? WHERE id=?",
                    (str(exc)[:1000], batch_id),
                )
            raise
        self.audit(
            "apply_schedule_snapshot",
            "schedule_sync_batch",
            batch_id,
            batch["diff"]["summary"],
        )
        return self.get_schedule_sync_batch(batch_id)

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
                "SELECT id FROM schedule_occurrences WHERE rule_id=? AND external_id=?",
                (values["rule_id"], values["external_id"]),
            ).fetchone()
            occurrence_id = existing["id"] if existing else _id()
            conn.execute(
                """INSERT INTO schedule_occurrences
                   (id, rule_id, course_id, occurrence_date, starts_at, ends_at, status, source_kind,
                    campus, building, room, teacher, notes, external_id, adjustment_external_id,
                    adjustment_of_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(rule_id, external_id) DO UPDATE SET
                     course_id=excluded.course_id, occurrence_date=excluded.occurrence_date,
                     starts_at=excluded.starts_at, ends_at=excluded.ends_at,
                     status=excluded.status, source_kind=excluded.source_kind,
                     campus=excluded.campus, building=excluded.building, room=excluded.room,
                     teacher=excluded.teacher, notes=excluded.notes, external_id=excluded.external_id,
                     adjustment_external_id=excluded.adjustment_external_id,
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
                    values.get("adjustment_external_id"),
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
        now = utc_now()
        created = False
        with self.db.connect(immediate=True) as conn:
            # 该无操作更新在 PostgreSQL 上取得行锁；SQLite 的 BEGIN IMMEDIATE 串行化写入。
            conn.execute(
                "UPDATE schedule_occurrences SET updated_at=updated_at WHERE id=?", (occurrence_id,)
            )
            occurrence = conn.execute(
                "SELECT * FROM schedule_occurrences WHERE id=?", (occurrence_id,)
            ).fetchone()
            if not occurrence:
                raise KeyError("schedule occurrence")
            existing = conn.execute(
                "SELECT session_id FROM session_automation WHERE occurrence_id=?", (occurrence_id,)
            ).fetchone()
            if existing:
                session_id = existing["session_id"]
            else:
                if occurrence["status"] == "cancelled":
                    raise ValueError("已停课的课堂安排不能创建 Session。")
                if occurrence["sync_status"] == "removed":
                    raise ValueError("已从课表移除的课堂安排不能创建 Session。")
                rule = conn.execute(
                    "SELECT * FROM schedule_rules WHERE id=?", (occurrence["rule_id"],)
                ).fetchone()
                if not rule:
                    raise KeyError("schedule rule")
                course_id = occurrence["course_id"] or rule["course_id"]
                if not course_id:
                    term = conn.execute(
                        "SELECT name FROM academic_terms WHERE id=?", (rule["term_id"],)
                    ).fetchone()
                    course = conn.execute(
                        "SELECT id FROM courses WHERE name=? AND semester=? ORDER BY created_at LIMIT 1",
                        (rule["course_name"], term["name"] if term else ""),
                    ).fetchone()
                    course_id = course["id"] if course else _id()
                    if not course:
                        conn.execute(
                            """INSERT INTO courses
                               (id, name, description, semester, teacher, schedule, profile_json,
                                created_at, updated_at) VALUES (?, ?, '', ?, ?, NULL, ?, ?, ?)""",
                            (
                                course_id,
                                rule["course_name"],
                                term["name"] if term else "",
                                rule["teacher"],
                                json.dumps(DEFAULT_PROFILE),
                                now,
                                now,
                            ),
                        )
                    conn.execute(
                        "UPDATE schedule_rules SET course_id=?, updated_at=? WHERE id=?",
                        (course_id, now, rule["id"]),
                    )
                    conn.execute(
                        "UPDATE schedule_occurrences SET course_id=?, updated_at=? WHERE id=?",
                        (course_id, now, occurrence_id),
                    )
                session_id = _id()
                title = f"{rule['course_name']}-{occurrence['occurrence_date']}-待识别"
                conn.execute(
                    """INSERT INTO sessions
                       (id, course_id, title, starts_at, ends_at, notes, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        course_id,
                        title,
                        occurrence["starts_at"],
                        occurrence["ends_at"],
                        occurrence["notes"] or "",
                        now,
                        now,
                    ),
                )
                conn.execute(
                    """INSERT INTO session_automation
                       (session_id, occurrence_id, materialization_reason, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (session_id, occurrence_id, reason, now, now),
                )
                created = True
        if created:
            self.audit("materialize_occurrence", "session", session_id, {"reason": reason})
        return self.db.get_session(session_id)

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
        now = utc_now()
        created = False
        with self.db.connect(immediate=True) as conn:
            conn.execute("UPDATE inbox_items SET updated_at=updated_at WHERE id=?", (item_id,))
            item = conn.execute("SELECT * FROM inbox_items WHERE id=?", (item_id,)).fetchone()
            if not item:
                raise KeyError("inbox item")
            if item["adopted_resource_id"]:
                resource_id = item["adopted_resource_id"]
            else:
                if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
                    raise KeyError("session")
                resource_id = _id()
                conn.execute(
                    """INSERT INTO resources
                       (id, session_id, type, evidence_level, name, mime_type, local_path,
                        storage_provider, storage_key, extracted_text, capture_range_json,
                        upload_state, created_at, updated_at)
                       VALUES (?, ?, ?, 'classroom', ?, ?, ?, ?, ?, ?, '[]', 'local_only', ?, ?)""",
                    (
                        resource_id,
                        session_id,
                        item["type"],
                        item["name"],
                        item["mime_type"],
                        item["local_path"],
                        item["storage_provider"],
                        item["storage_key"],
                        item["extracted_text"] or "",
                        now,
                        now,
                    ),
                )
                created = True
            conn.execute(
                """UPDATE inbox_items SET matching_status='accepted', adopted_resource_id=?,
                   suggested_session_id=?, locked=?, archived=1, updated_at=? WHERE id=?""",
                (resource_id, session_id, int(lock), now, item_id),
            )
        if created:
            self.audit("adopt_inbox_item", "inbox_item", item_id, {"session_id": session_id})
        return self.db.get_resource(resource_id)

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
        with self.db.connect(immediate=True) as conn:
            existing = conn.execute(
                """SELECT * FROM review_items WHERE kind=? AND subject_type=? AND subject_id=?
                   AND status IN ('pending', 'later') ORDER BY created_at DESC""",
                (kind, subject_type, subject_id),
            ).fetchone()
            if existing:
                return _decode(existing)  # type: ignore[return-value]
            now, review_id = utc_now(), _id()
            conn.execute(
                """INSERT INTO review_items
                   (id, kind, subject_type, subject_id, title, proposed_value, confidence,
                    reasons_json, navigation_path, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT DO NOTHING""",
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
            row = conn.execute(
                """SELECT * FROM review_items WHERE kind=? AND subject_type=? AND subject_id=?
                   AND status IN ('pending', 'later') ORDER BY created_at DESC""",
                (kind, subject_type, subject_id),
            ).fetchone()
        return _decode(row)  # type: ignore[return-value]

    def list_review_items(self, status: str = "pending") -> list[dict[str, Any]]:
        if status not in {"pending", "later", "accepted", "rejected", "all"}:
            raise ValueError("不支持的审核状态。")
        query, args = "SELECT * FROM review_items", ()
        if status != "all":
            query, args = query + " WHERE status=?", (status,)
        with self.db.connect() as conn:
            rows = conn.execute(query + " ORDER BY updated_at DESC, created_at DESC", args).fetchall()
        return [_decode(row) for row in rows]  # type: ignore[misc]

    def get_review_item(self, review_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM review_items WHERE id=?", (review_id,)).fetchone()
        if not row:
            raise KeyError("review item")
        return _decode(row)  # type: ignore[return-value]

    def open_review_item(
        self, kind: str, subject_type: str, subject_id: str
    ) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """SELECT * FROM review_items WHERE kind=? AND subject_type=? AND subject_id=?
                   AND status IN ('pending', 'later') ORDER BY created_at DESC LIMIT 1""",
                (kind, subject_type, subject_id),
            ).fetchone()
        return _decode(row) if row else None

    def decide_review(
        self,
        review_id: str,
        action: str,
        reason: str = "",
        snoozed_until: str | None = None,
    ) -> dict[str, Any]:
        target = {
            "accept": "accepted",
            "edit_accept": "accepted",
            "reject": "rejected",
            "later": "later",
            "pending": "pending",
        }[action]
        changed = False
        with self.db.connect(immediate=True) as conn:
            conn.execute("UPDATE review_items SET updated_at=updated_at WHERE id=?", (review_id,))
            row = conn.execute("SELECT * FROM review_items WHERE id=?", (review_id,)).fetchone()
            if not row:
                raise KeyError("review item")
            current = _decode(row)
            if current["status"] in {"accepted", "rejected"}:
                return current  # type: ignore[return-value]
            if current["status"] == target and (
                target != "later" or current.get("snoozed_until") == snoozed_until
            ):
                return current  # type: ignore[return-value]
            conn.execute(
                """UPDATE review_items SET status=?, decision_reason=?, decided_at=?,
                   snoozed_until=?, updated_at=? WHERE id=?""",
                (
                    target,
                    reason,
                    None if target in {"pending", "later"} else utc_now(),
                    snoozed_until if target == "later" else None,
                    utc_now(),
                    review_id,
                ),
            )
            changed = True
        if changed:
            self.audit(
                "review_decision",
                "review_item",
                review_id,
                {
                    "action": action,
                    "from_status": current["status"],
                    "to_status": target,
                    "reason": reason,
                    "snoozed_until": snoozed_until,
                },
            )
        with self.db.connect() as conn:
            updated = conn.execute("SELECT * FROM review_items WHERE id=?", (review_id,)).fetchone()
        return _decode(updated)  # type: ignore[return-value]

    def apply_review_decision(
        self,
        review_id: str,
        action: str,
        *,
        edited_value: str | None = None,
        reason: str = "",
        snoozed_until: str | None = None,
        fail_after_side_effect: bool = False,
    ) -> dict[str, Any]:
        """Apply a review decision and its domain side effect in one transaction.

        ``fail_after_side_effect`` is an intentional test seam proving that neither
        the resource/title change nor audit rows survive a mid-decision failure.
        """

        target = {
            "accept": "accepted",
            "edit_accept": "accepted",
            "reject": "rejected",
            "later": "later",
            "pending": "pending",
        }[action]
        with self.db.connect(immediate=True) as conn:
            # PostgreSQL obtains a row lock; SQLite serializes writers via BEGIN IMMEDIATE.
            conn.execute("UPDATE review_items SET updated_at=updated_at WHERE id=?", (review_id,))
            row = conn.execute("SELECT * FROM review_items WHERE id=?", (review_id,)).fetchone()
            if not row:
                raise KeyError("review item")
            current = _decode(row)
            if current["status"] in {"accepted", "rejected"}:
                return current  # type: ignore[return-value]
            if current["status"] == target and (
                target != "later" or current.get("snoozed_until") == snoozed_until
            ):
                return current  # type: ignore[return-value]

            timestamp = utc_now()
            if action in {"accept", "edit_accept"}:
                value = edited_value or current.get("proposed_value")
                if current["kind"] == "archive_match":
                    if not value:
                        raise ValueError("请先选择 Session")
                    conn.execute(
                        "UPDATE inbox_items SET updated_at=updated_at WHERE id=?",
                        (current["subject_id"],),
                    )
                    item = conn.execute(
                        "SELECT * FROM inbox_items WHERE id=?", (current["subject_id"],)
                    ).fetchone()
                    if not item:
                        raise KeyError("inbox item")
                    created_resource = False
                    if item["adopted_resource_id"]:
                        resource_id = item["adopted_resource_id"]
                        adopted = conn.execute(
                            "SELECT session_id FROM resources WHERE id=?", (resource_id,)
                        ).fetchone()
                        if not adopted:
                            raise KeyError("resource")
                        if adopted["session_id"] != value:
                            raise ValueError("该资料已被归档到另一个 Session，请刷新后确认。")
                    else:
                        if not conn.execute(
                            "SELECT 1 FROM sessions WHERE id=?", (value,)
                        ).fetchone():
                            raise KeyError("session")
                        resource_id = _id()
                        conn.execute(
                            """INSERT INTO resources
                               (id, session_id, type, evidence_level, name, mime_type, local_path,
                                storage_provider, storage_key, extracted_text, capture_range_json,
                                upload_state, created_at, updated_at)
                               VALUES (?, ?, ?, 'classroom', ?, ?, ?, ?, ?, ?, '[]',
                                       'local_only', ?, ?)""",
                            (
                                resource_id,
                                value,
                                item["type"],
                                item["name"],
                                item["mime_type"],
                                item["local_path"],
                                item["storage_provider"],
                                item["storage_key"],
                                item["extracted_text"] or "",
                                timestamp,
                                timestamp,
                            ),
                        )
                        created_resource = True
                    conn.execute(
                        """UPDATE inbox_items SET matching_status='accepted', adopted_resource_id=?,
                           suggested_session_id=?, locked=1, archived=1, updated_at=? WHERE id=?""",
                        (resource_id, value, timestamp, current["subject_id"]),
                    )
                    conn.execute(
                        """INSERT INTO resource_automation
                           (resource_id, transcription_state, auto_transcribe, created_at, updated_at)
                           VALUES (?, 'saved', 1, ?, ?)
                           ON CONFLICT(resource_id) DO UPDATE SET updated_at=excluded.updated_at""",
                        (resource_id, timestamp, timestamp),
                    )
                    if created_resource:
                        conn.execute(
                            "INSERT INTO audit_log VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                _id(),
                                "adopt_inbox_item",
                                "inbox_item",
                                current["subject_id"],
                                json.dumps({"session_id": value}, ensure_ascii=False),
                                timestamp,
                            ),
                        )
                elif current["kind"] == "session_topic":
                    if not value or not str(value).strip():
                        raise ValueError("主题标题不能为空")
                    session = conn.execute(
                        "SELECT 1 FROM sessions WHERE id=?", (current["subject_id"],)
                    ).fetchone()
                    if not session:
                        raise KeyError("session")
                    conn.execute(
                        """INSERT INTO session_automation (session_id, created_at, updated_at)
                           VALUES (?, ?, ?) ON CONFLICT(session_id) DO NOTHING""",
                        (current["subject_id"], timestamp, timestamp),
                    )
                    title_state = conn.execute(
                        "SELECT title_locked FROM session_automation WHERE session_id=?",
                        (current["subject_id"],),
                    ).fetchone()
                    should_update = action == "edit_accept" or not title_state["title_locked"]
                    if should_update:
                        source = "user_review" if action == "edit_accept" else "transcript_rule"
                        confidence = 1 if action == "edit_accept" else current["confidence"]
                        conn.execute(
                            "UPDATE sessions SET title=?, updated_at=? WHERE id=?",
                            (str(value).strip(), timestamp, current["subject_id"]),
                        )
                        conn.execute(
                            """UPDATE session_automation SET title_source=?, title_confidence=?,
                               title_locked=?, topic_candidate=?, updated_at=? WHERE session_id=?""",
                            (
                                source,
                                confidence,
                                int(action == "edit_accept"),
                                str(value).strip(),
                                timestamp,
                                current["subject_id"],
                            ),
                        )
                        conn.execute(
                            "INSERT INTO audit_log VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                _id(),
                                "update_session_title",
                                "session",
                                current["subject_id"],
                                json.dumps(
                                    {"source": source, "confidence": confidence},
                                    ensure_ascii=False,
                                ),
                                timestamp,
                            ),
                        )
            if fail_after_side_effect:
                raise RuntimeError("simulated review side-effect interruption")

            conn.execute(
                """UPDATE review_items SET status=?, decision_reason=?, decided_at=?,
                   snoozed_until=?, updated_at=? WHERE id=?""",
                (
                    target,
                    reason,
                    None if target in {"pending", "later"} else timestamp,
                    snoozed_until if target == "later" else None,
                    timestamp,
                    review_id,
                ),
            )
            conn.execute(
                "INSERT INTO audit_log VALUES (?, ?, ?, ?, ?, ?)",
                (
                    _id(),
                    "review_decision",
                    "review_item",
                    review_id,
                    json.dumps(
                        {
                            "action": action,
                            "from_status": current["status"],
                            "to_status": target,
                            "reason": reason,
                            "snoozed_until": snoozed_until,
                        },
                        ensure_ascii=False,
                    ),
                    timestamp,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM review_items WHERE id=?", (review_id,)
            ).fetchone()
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
            profile_id = values.get("provider_profile_id")
            if profile_id and not conn.execute(
                "SELECT 1 FROM provider_profiles WHERE id=?", (profile_id,)
            ).fetchone():
                # 测试注入与内建 Provider 没有持久化 Profile；日志仍保留名称和模型。
                profile_id = None
            foreign_values = {
                "job_id": ("jobs", values.get("job_id")),
                "resource_id": ("resources", values.get("resource_id")),
                "session_id": ("sessions", values.get("session_id")),
            }
            for key, (table, foreign_id) in foreign_values.items():
                if foreign_id and not conn.execute(
                    f"SELECT 1 FROM {table} WHERE id=?", (foreign_id,)
                ).fetchone():
                    foreign_values[key] = (table, None)
            conn.execute(
                """INSERT INTO provider_call_logs
                   (id, operation, provider_profile_id, provider_name, model, job_id, resource_id,
                    session_id, status, duration_ms, request_count, audio_minutes, input_tokens,
                    output_tokens, estimated_cost, cost_currency, cost_known, error_type, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    log_id,
                    values["operation"],
                    profile_id,
                    values["provider_name"],
                    values.get("model"),
                    foreign_values["job_id"][1],
                    foreign_values["resource_id"][1],
                    foreign_values["session_id"][1],
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
