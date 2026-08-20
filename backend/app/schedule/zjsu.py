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
            raise ValueError("login mode must be account, sso or qr")
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
                raise ValueError(f"fixture term.{key} is required")
        courses = payload.get("courses")
        if not isinstance(courses, list):
            raise ValueError("fixture courses must be a list")
        period_times = self._period_times(payload.get("period_times"))
        parsed_rules: list[dict[str, Any]] = []
        parsed_occurrences: list[dict[str, Any]] = []
        for index, item in enumerate(courses):
            rule = self._parse_course(item, index)
            parsed_rules.append(rule)
            parsed_occurrences.extend(self._occurrences(term, rule, period_times))
        for adjustment in payload.get("adjustments", []):
            parsed_occurrences.append(self._parse_adjustment(adjustment, period_times))
        return {"term": term, "rules": parsed_rules, "occurrences": parsed_occurrences}

    def _parse_course(self, item: dict[str, Any], index: int) -> dict[str, Any]:
        name = str(item.get("course_name") or "").strip()
        external_id = str(item.get("external_id") or "").strip()
        if not name or not external_id:
            raise ValueError(f"courses[{index}] requires course_name and external_id")
        weekday = int(item.get("weekday", 0))
        start_period = int(item.get("start_period", 0))
        end_period = int(item.get("end_period", 0))
        if weekday not in range(1, 8) or start_period < 1 or end_period < start_period:
            raise ValueError(f"courses[{index}] has invalid weekday or periods")
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
                    "rule_external_id": rule["external_id"],
                    "external_id": f"{rule['external_id']}:{day.isoformat()}:{rule['start_period']}-{rule['end_period']}",
                }
            )
        return occurrences

    def _parse_adjustment(
        self, item: dict[str, Any], period_times: dict[int, tuple[str, str]]
    ) -> dict[str, Any]:
        for key in ("rule_external_id", "date", "external_id"):
            if not item.get(key):
                raise ValueError(f"adjustment.{key} is required")
        status = item.get("status", "scheduled")
        source_kind = item.get("source_kind", "adjustment")
        if status not in {"scheduled", "cancelled"} or source_kind not in {"adjustment", "makeup"}:
            raise ValueError("adjustment status/source_kind is invalid")
        start_period = int(item.get("start_period", 1))
        end_period = int(item.get("end_period", start_period))
        timezone = ZoneInfo(item.get("timezone", "Asia/Shanghai"))
        day = date.fromisoformat(item["date"])
        starts = datetime.combine(day, self._clock(period_times, start_period, 0), timezone)
        ends = datetime.combine(day, self._clock(period_times, end_period, 1), timezone)
        return {
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
                raise ValueError("period_times values must be [start, end]")
            result[int(key)] = (value[0], value[1])
        return result

    @staticmethod
    def _clock(period_times: dict[int, tuple[str, str]], period: int, index: int) -> time:
        if period not in period_times:
            raise ValueError(f"fixture has no time definition for period {period}")
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
            raise ValueError("weeks must be a list or a string such as 1-16周(双)")
        if not weeks or any(item < 1 or item > 60 for item in weeks):
            raise ValueError("weeks must contain values between 1 and 60")
        if odd_even not in {"all", "odd", "even"}:
            raise ValueError("odd_even must be all, odd or even")
        return weeks, odd_even
