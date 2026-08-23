from __future__ import annotations

import json
import os
import platform
import re
import sys
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from .automation import AutomationRepository
from .database import Database

REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(?i)(^x-|authorization|proxy.authorization|cookie|set.cookie|api.?key|access.?key|"
    r"secret|password|passwd|token|credential|encryption.?key|custom.?headers|session.?cipher)"
)
_HEADER_VALUE = re.compile(
    r"(?im)^(\s*(?:x-[a-z0-9-]+|authorization|proxy-authorization|cookie|set-cookie|"
    r"api-key|access-token|refresh-token)\s*[:=]\s*).*$"
)
_JSON_VALUE = re.compile(
    r'(?i)(["\'](?:x-[a-z0-9-]+|authorization|cookie|api[_-]?key|access[_-]?key|secret|password|'
    r'token|credential|encryption[_-]?key|custom[_-]?headers)["\']\s*:\s*)'
    r'(["\'][^"\']*["\']|[^,}\]\s]+)'
)
_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_.-]*(?:API.?KEY|ACCESS.?KEY|SECRET|PASSWORD|TOKEN|CREDENTIAL)"
    r"[A-Z0-9_.-]*\s*=\s*)[^\s,;]+"
)
_URL_SECRET = re.compile(
    r"(?i)([?&](?:access_token|refresh_token|token|api_key|key|secret|signature|credential)=)"
    r"[^&#\s]+"
)
_BEARER = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]+")
_SK_TOKEN = re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{8,}")
_CUSTOM_HEADERS_OBJECT = re.compile(
    r'(?i)(["\']custom[_-]?headers["\']\s*:\s*)\{[^}\r\n]*\}'
)


def redact_text(value: str) -> str:
    """Redact credentials in logs, JSON fragments, URLs and stack traces."""

    value = _CUSTOM_HEADERS_OBJECT.sub(
        lambda match: f'{match.group(1)}"{REDACTED}"', value
    )
    value = _HEADER_VALUE.sub(lambda match: f"{match.group(1)}{REDACTED}", value)
    value = _JSON_VALUE.sub(lambda match: f'{match.group(1)}"{REDACTED}"', value)
    value = _ASSIGNMENT.sub(lambda match: f"{match.group(1)}{REDACTED}", value)
    value = _URL_SECRET.sub(lambda match: f"{match.group(1)}{REDACTED}", value)
    value = _BEARER.sub(lambda match: f"{match.group(1)}{REDACTED}", value)
    return _SK_TOKEN.sub(REDACTED, value)


def redact_structure(value: Any, key: str = "") -> Any:
    if _SENSITIVE_KEY.search(key):
        return REDACTED
    if isinstance(value, dict):
        return {str(item_key): redact_structure(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [redact_structure(item) for item in value]
    if isinstance(value, tuple):
        return [redact_structure(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class DiagnosticExporter:
    """Build a local-only support bundle from an explicit content whitelist."""

    LOG_INPUT_LIMIT = 512 * 1024
    LOG_OUTPUT_LIMIT = 200 * 1024
    LOG_TOTAL_LIMIT = 800 * 1024
    _LOG_NAME = re.compile(r"^(backend|web)\.log(?:\.[1-5])?$")

    def __init__(
        self,
        db: Database,
        automation: AutomationRepository,
        data_dir: Path,
        *,
        app_version: str,
    ) -> None:
        self.db = db
        self.automation = automation
        self.data_dir = data_dir.resolve()
        self.output_dir = self.data_dir / "diagnostics"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.chmod(0o700)
        self.app_version = app_version

    def build(self, *, fail_after_write: bool = False) -> tuple[Path, str]:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
        download_name = f"KnowledgeDebt-diagnostics-{stamp}.zip"
        temporary = self.output_dir / f".diagnostic-{uuid.uuid4().hex}.tmp"
        final = self.output_dir / f"diagnostic-{uuid.uuid4().hex}.zip"
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                self._write_json(archive, "manifest.json", self._manifest())
                if fail_after_write:
                    raise RuntimeError("simulated diagnostic export failure")
                remaining = self.LOG_TOTAL_LIMIT
                for path in self._allowed_logs():
                    if remaining <= 0:
                        break
                    content = self._safe_log_tail(path, min(self.LOG_OUTPUT_LIMIT, remaining))
                    if content:
                        final_content = redact_text(content)
                        archive.writestr(f"logs/{path.name}", final_content)
                        remaining -= len(final_content.encode("utf-8"))
                self._write_json(
                    archive,
                    "contents.json",
                    {
                        "included": ["manifest.json", "contents.json", "logs/backend.log*", "logs/web.log*"],
                        "never_included": [
                            "database", "course_schedule", "transcripts", "audio",
                            "documents", "notes", "provider_secrets",
                        ],
                        "uploaded": False,
                    },
                )
            os.chmod(temporary, 0o600)
            os.replace(temporary, final)
            final.chmod(0o600)
            return final, download_name
        except Exception:
            temporary.unlink(missing_ok=True)
            final.unlink(missing_ok=True)
            raise

    @staticmethod
    def discard(path: Path) -> None:
        path.unlink(missing_ok=True)

    def _manifest(self) -> dict[str, Any]:
        has_migration_table = (
            sa.inspect(self.db.engine).has_table("alembic_version")
            if self.db.engine is not None
            else self._sqlite_has_table("alembic_version")
        )
        with self.db.connect() as conn:
            migration = (
                conn.execute(
                    "SELECT version_num FROM alembic_version ORDER BY version_num DESC"
                ).fetchone()
                if has_migration_table
                else None
            )
            profiles = conn.execute(
                """SELECT id, vendor, adapter, default_model, external, enabled,
                          implementation_status, last_test_status
                   FROM provider_profiles ORDER BY id"""
            ).fetchall()
            jobs = conn.execute(
                "SELECT kind, status, COUNT(*) AS count FROM jobs GROUP BY kind, status"
            ).fetchall()
        settings = self.automation.get_app_settings()
        manifest = {
            "generated_at": datetime.now(UTC).isoformat(),
            "application": {"name": "KnowledgeDebt", "version": self.app_version},
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "python": platform.python_version(),
                "frozen": bool(getattr(sys, "frozen", False)),
            },
            "database": {"migration": migration["version_num"] if migration else None, "reachable": True},
            "components": {"api": "ready", "storage": "ready"},
            "feature_flags": {
                "auto_transcribe": bool(settings["auto_transcribe"]),
                "recording_chunk_retention_days": settings["recording_chunk_retention_days"],
            },
            "providers": [dict(row) for row in profiles],
            "jobs": [dict(row) for row in jobs],
        }
        return redact_structure(manifest)

    def _sqlite_has_table(self, name: str) -> bool:
        with self.db.connect() as conn:
            return bool(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
                ).fetchone()
            )

    @staticmethod
    def _write_json(archive: zipfile.ZipFile, name: str, value: Any) -> None:
        safe = redact_structure(value)
        archive.writestr(name, json.dumps(safe, ensure_ascii=False, indent=2))

    def _allowed_logs(self) -> list[Path]:
        root = self.data_dir / "logs"
        if not root.is_dir() or root.is_symlink():
            return []
        return sorted(
            (
                item for item in root.iterdir()
                if self._LOG_NAME.fullmatch(item.name) and item.is_file() and not item.is_symlink()
            ),
            key=lambda item: item.name,
        )

    def _safe_log_tail(self, path: Path, output_limit: int) -> str:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            size = os.fstat(descriptor).st_size
            offset = max(0, size - self.LOG_INPUT_LIMIT)
            os.lseek(descriptor, offset, os.SEEK_SET)
            raw = os.read(descriptor, self.LOG_INPUT_LIMIT)
        finally:
            os.close(descriptor)
        text = raw.decode("utf-8", errors="replace")
        if offset:
            _, separator, text = text.partition("\n")
            if not separator:
                return ""
        redacted = redact_text(text)
        encoded = redacted.encode("utf-8")
        if len(encoded) <= output_limit:
            return redacted
        tail = encoded[-output_limit:].decode("utf-8", errors="ignore")
        _, separator, tail = tail.partition("\n")
        return tail if separator else ""
