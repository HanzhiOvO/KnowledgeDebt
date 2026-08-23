from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Asia/Shanghai"


def local_date(timezone: str = DEFAULT_TIMEZONE, at: datetime | None = None) -> date:
    instant = at or datetime.now(UTC)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(ZoneInfo(timezone)).date()
