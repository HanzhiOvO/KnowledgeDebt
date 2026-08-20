"""Add v0.2 automated course workbench data model."""

from __future__ import annotations

from sqlalchemy import text

from alembic import op
from app.automation_schema import AUTOMATION_SCHEMA

revision = "20260815_0002"
down_revision = "20260815_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in AUTOMATION_SCHEMA.split(";"):
        statement = statement.strip()
        if statement:
            op.execute(text(statement))


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS idx_one_active_transcription_per_resource"))
    for table in (
        "audit_log",
        "provider_call_logs",
        "review_items",
        "inbox_items",
        "transcription_chunks",
        "resource_automation",
        "session_automation",
        "schedule_occurrences",
        "schedule_rules",
        "schedule_connections",
        "academic_terms",
        "provider_defaults",
        "provider_profiles",
    ):
        op.execute(text(f"DROP TABLE IF EXISTS {table}"))

