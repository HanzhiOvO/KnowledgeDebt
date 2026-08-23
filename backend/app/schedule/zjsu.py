from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ConnectorCapability:
    live_login: bool
    fixture_import: bool
    reason: str


class ZJSUConnector:
    """Safe connector boundary for 浙江工商大学本科教务系统 V-9.0.

    Live endpoints are intentionally absent until an authorized, sanitized HAR or test
    account proves a stable interactive flow. This prevents guessed endpoints, CAPTCHA
    bypasses, cookie leakage, and brittle high-frequency scraping.
    """

    connector_id = "zjsu_undergraduate_v9"
    base_url = "https://jwxt.zjgsu.edu.cn/jwglxt"

    @property
    def capability(self) -> ConnectorCapability:
        return ConnectorCapability(
            live_login=False,
            fixture_import=True,
            reason="需要用户授权的脱敏 HAR 或测试账号验证登录、验证码/SSO 与课表响应；当前不猜测接口。",
        )

    def begin_login(self, mode: str) -> dict[str, Any]:
        if mode not in {"account", "sso", "qr"}:
            raise ValueError("登录方式只支持账号、SSO 或扫码。")
        return {
            "state": "fixture_required",
            "mode": mode,
            "base_url": self.base_url,
            "reauth_required": False,
            "message": self.capability.reason,
        }


class ZJSUFixtureParser:
    """Parses a versioned, sanitized fixture without retaining login credentials."""

    DEFAULT_PERIOD_TIMES = {
        1: ("08:00", "08:45"),
        2: ("08:50", "09:35"),
        3: ("09:50", "10:35"),
        4: ("10:40", "11:25"),
        5: ("11:30", "12:15"),
        6: ("13:30", "14:15"),
        7: ("14:20", "15:05"),
        8: ("15:20", "16:05"),
        9: ("16:10", "16:55"),
        10: ("18:30", "19:15"),
        11: ("19:20", "20:05"),
        12: ("20:10", "20:55"),
    }

    def parse(self, raw: bytes | str) -> dict[str, Any]:
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("课表 fixture 必须是 UTF-8 JSON") from exc
        if payload.get("schema") != "knowledgedebt.zjsu.schedule.fixture.v1":
            raise ValueError("不支持的 fixture schema；需要 knowledgedebt.zjsu.schedule.fixture.v1")
        term = payload.get("term") or {}
        for key in ("name", "starts_on", "ends_on"):
            if not term.get(key):
                raise ValueError(f"课表样例缺少 term.{key}。")
        courses = payload.get("courses")
        if not isinstance(courses, list):
            raise ValueError("课表样例的 courses 必须是数组。")
        period_times = self._period_times(payload.get("period_times"))
        parsed_rules: list[dict[str, Any]] = []
        parsed_occurrences: list[dict[str, Any]] = []
        for index, item in enumerate(courses):
            rule = self._parse_course(item, index)
            parsed_rules.append(rule)
            parsed_occurrences.extend(self._occurrences(term, rule, period_times))
        for index, adjustment in enumerate(payload.get("adjustments", [])):
            parsed = self._parse_adjustment(
                adjustment,
                period_times,
                term.get("timezone", "Asia/Shanghai"),
            )
            self._apply_adjustment(parsed_occurrences, parsed, index)
        identities: set[tuple[str, str]] = set()
        time_slots: set[tuple[str, str, str, str]] = set()
        for occurrence in parsed_occurrences:
            identity = (occurrence["rule_external_id"], occurrence["external_id"])
            slot = (
                occurrence["rule_external_id"],
                occurrence["occurrence_date"],
                occurrence["starts_at"],
                occurrence["ends_at"],
            )
            if identity in identities:
                raise ValueError(f"课表实例 external_id 重复：{occurrence['external_id']}")
            if slot in time_slots:
                raise ValueError(
                    "同一课程规则在同一日期和时段产生了多个有效课次；请检查调停课来源数据。"
                )
            identities.add(identity)
            time_slots.add(slot)
        return {"term": term, "rules": parsed_rules, "occurrences": parsed_occurrences}

    def _parse_course(self, item: dict[str, Any], index: int) -> dict[str, Any]:
        name = str(item.get("course_name") or "").strip()
        external_id = str(item.get("external_id") or "").strip()
        if not name or not external_id:
            raise ValueError(f"courses[{index}] 必须包含 course_name 和 external_id。")
        weekday = int(item.get("weekday", 0))
        start_period = int(item.get("start_period", 0))
        end_period = int(item.get("end_period", 0))
        if weekday not in range(1, 8) or start_period < 1 or end_period < start_period:
            raise ValueError(f"courses[{index}] 的星期或节次无效。")
        weeks, odd_even = self._weeks(item.get("weeks"), item.get("odd_even"))
        return {
            "course_name": name,
            "course_code": item.get("course_code"),
            "class_name": item.get("class_name"),
            "teacher": item.get("teacher"),
            "campus": item.get("campus"),
            "building": item.get("building"),
            "room": item.get("room"),
            "weekday": weekday,
            "start_period": start_period,
            "end_period": end_period,
            "weeks": weeks,
            "odd_even": odd_even,
            "notes": item.get("notes", ""),
            "external_id": external_id,
            "aliases": item.get("aliases", []),
        }

    def _occurrences(
        self,
        term: dict[str, Any],
        rule: dict[str, Any],
        period_times: dict[int, tuple[str, str]],
    ) -> list[dict[str, Any]]:
        term_start = date.fromisoformat(term["starts_on"])
        week_one_monday = term_start - timedelta(days=term_start.weekday())
        timezone = ZoneInfo(term.get("timezone", "Asia/Shanghai"))
        start_clock = self._clock(period_times, rule["start_period"], 0)
        end_clock = self._clock(period_times, rule["end_period"], 1)
        occurrences: list[dict[str, Any]] = []
        for week in rule["weeks"]:
            if rule["odd_even"] == "odd" and week % 2 == 0:
                continue
            if rule["odd_even"] == "even" and week % 2 == 1:
                continue
            day = week_one_monday + timedelta(weeks=week - 1, days=rule["weekday"] - 1)
            starts = datetime.combine(day, start_clock, timezone)
            ends = datetime.combine(day, end_clock, timezone)
            occurrences.append(
                {
                    "occurrence_date": day.isoformat(),
                    "starts_at": starts.isoformat(),
                    "ends_at": ends.isoformat(),
                    "status": "scheduled",
                    "source_kind": "regular",
                    "campus": rule.get("campus"),
                    "building": rule.get("building"),
                    "room": rule.get("room"),
                    "teacher": rule.get("teacher"),
                    "notes": rule.get("notes", ""),
                    "rule_external_id": rule["external_id"],
                    # 节次变化仍代表同一门课的同一天课堂；稳定 ID 让快照把它识别为修改而非删除+新增。
                    "external_id": f"{rule['external_id']}:{day.isoformat()}",
                }
            )
        return occurrences

    def _parse_adjustment(
        self,
        item: dict[str, Any],
        period_times: dict[int, tuple[str, str]],
        default_timezone: str,
    ) -> dict[str, Any]:
        for key in ("rule_external_id", "date", "external_id"):
            if not item.get(key):
                raise ValueError(f"调课记录缺少 adjustment.{key}。")
        status = item.get("status", "scheduled")
        source_kind = item.get("source_kind", "adjustment")
        if status not in {"scheduled", "cancelled"} or source_kind not in {"adjustment", "makeup"}:
            raise ValueError("调课记录的 status 或 source_kind 无效。")
        start_period = int(item.get("start_period", 1))
        end_period = int(item.get("end_period", start_period))
        timezone = ZoneInfo(item.get("timezone", default_timezone))
        day = date.fromisoformat(item["date"])
        starts = datetime.combine(day, self._clock(period_times, start_period, 0), timezone)
        ends = datetime.combine(day, self._clock(period_times, end_period, 1), timezone)
        result = {
            "rule_external_id": item["rule_external_id"],
            "occurrence_date": day.isoformat(),
            "starts_at": starts.isoformat(),
            "ends_at": ends.isoformat(),
            "status": status,
            "source_kind": source_kind,
            "campus": item.get("campus"),
            "building": item.get("building"),
            "room": item.get("room"),
            "teacher": item.get("teacher"),
            "notes": item.get("notes", ""),
            "external_id": item["external_id"],
            "adjustment_external_id": item["external_id"],
            "adjustment_of_external_id": item.get("adjustment_of_external_id"),
        }
        original_date = item.get("original_date")
        if original_date:
            original_day = date.fromisoformat(original_date)
            original_start_period = int(item.get("original_start_period", start_period))
            original_end_period = int(item.get("original_end_period", end_period))
            result["original_occurrence_date"] = original_day.isoformat()
            result["original_starts_at"] = datetime.combine(
                original_day,
                self._clock(period_times, original_start_period, 0),
                timezone,
            ).isoformat()
            result["original_ends_at"] = datetime.combine(
                original_day,
                self._clock(period_times, original_end_period, 1),
                timezone,
            ).isoformat()
        return result

    @classmethod
    def _apply_adjustment(
        cls,
        occurrences: list[dict[str, Any]],
        adjustment: dict[str, Any],
        index: int,
    ) -> None:
        """Fold source adjustments into one conflict-free effective occurrence snapshot.

        A cancellation annotates the stable regular occurrence instead of adding a
        duplicate row. A moved lesson keeps the cancelled original and adds a linked
        target. A makeup lesson is an independent extra occurrence.
        """

        if adjustment["source_kind"] == "makeup":
            occurrences.append(cls._public_adjustment_fields(adjustment))
            return

        original = cls._find_adjusted_occurrence(occurrences, adjustment)
        if adjustment["status"] == "cancelled":
            if original is None:
                occurrences.append(cls._public_adjustment_fields(adjustment))
                return
            replacement = cls._overlay_original(original, adjustment, status="cancelled")
            occurrences[occurrences.index(original)] = replacement
            return

        if original is None:
            raise ValueError(
                f"adjustments[{index}] 是调课，但没有通过原日期/节次或 adjustment_of_external_id "
                "定位被调整的原课次。"
            )
        same_slot = all(
            original[key] == adjustment[key]
            for key in ("occurrence_date", "starts_at", "ends_at")
        )
        if same_slot:
            occurrences[occurrences.index(original)] = cls._overlay_original(
                original,
                adjustment,
                status="scheduled",
            )
            return
        occurrences[occurrences.index(original)] = cls._overlay_original(
            original,
            adjustment,
            status="cancelled",
        )
        target = cls._public_adjustment_fields(adjustment)
        target["adjustment_of_external_id"] = original["external_id"]
        occurrences.append(target)

    @staticmethod
    def _find_adjusted_occurrence(
        occurrences: list[dict[str, Any]], adjustment: dict[str, Any]
    ) -> dict[str, Any] | None:
        candidates = [
            item
            for item in occurrences
            if item["rule_external_id"] == adjustment["rule_external_id"]
        ]
        explicit = adjustment.get("adjustment_of_external_id")
        if explicit:
            return next((item for item in candidates if item["external_id"] == explicit), None)
        if adjustment.get("original_occurrence_date"):
            return next(
                (
                    item
                    for item in candidates
                    if item["occurrence_date"] == adjustment["original_occurrence_date"]
                    and item["starts_at"] == adjustment["original_starts_at"]
                    and item["ends_at"] == adjustment["original_ends_at"]
                ),
                None,
            )
        return next(
            (
                item
                for item in candidates
                if item["occurrence_date"] == adjustment["occurrence_date"]
                and item["starts_at"] == adjustment["starts_at"]
                and item["ends_at"] == adjustment["ends_at"]
            ),
            None,
        )

    @staticmethod
    def _overlay_original(
        original: dict[str, Any], adjustment: dict[str, Any], *, status: str
    ) -> dict[str, Any]:
        result = {
            **original,
            "status": status,
            "source_kind": "adjustment",
            "adjustment_external_id": adjustment["external_id"],
        }
        if status == "scheduled":
            for key in ("campus", "building", "room", "teacher"):
                if adjustment.get(key) is not None:
                    result[key] = adjustment[key]
        if adjustment.get("notes"):
            result["notes"] = adjustment["notes"]
        return result

    @staticmethod
    def _public_adjustment_fields(adjustment: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in adjustment.items()
            if not key.startswith("original_")
        }

    @classmethod
    def _period_times(cls, values: Any) -> dict[int, tuple[str, str]]:
        if not values:
            raise ValueError(
                "fixture.period_times is required; KnowledgeDebt will not guess the university's current period clock"
            )
        result: dict[int, tuple[str, str]] = {}
        for key, value in values.items():
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError("period_times 的每一项必须是 [开始时间, 结束时间]。")
            result[int(key)] = (value[0], value[1])
        return result

    @staticmethod
    def _clock(period_times: dict[int, tuple[str, str]], period: int, index: int) -> time:
        if period not in period_times:
            raise ValueError(f"课表样例没有定义第 {period} 节的时间。")
        return time.fromisoformat(period_times[period][index])

    @staticmethod
    def _weeks(value: Any, explicit_odd_even: Any) -> tuple[list[int], str]:
        odd_even = str(explicit_odd_even or "all").lower()
        if isinstance(value, list):
            weeks = sorted({int(item) for item in value})
        elif isinstance(value, str):
            odd_even = "odd" if "单" in value else "even" if "双" in value else odd_even
            weeks = []
            for start, end in re.findall(r"(\d+)(?:-(\d+))?", value):
                first, last = int(start), int(end or start)
                weeks.extend(range(first, last + 1))
            weeks = sorted(set(weeks))
        else:
            raise ValueError("weeks 必须是数组，或类似“1-16周(双)”的字符串。")
        if not weeks or any(item < 1 or item > 60 for item in weeks):
            raise ValueError("weeks 中的周次必须在 1 到 60 之间。")
        if odd_even not in {"all", "odd", "even"}:
            raise ValueError("odd_even 只支持 all、odd 或 even。")
        return weeks, odd_even
