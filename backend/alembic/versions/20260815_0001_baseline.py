"""Create the KnowledgeDebt 0.1 application schema."""

from __future__ import annotations

from sqlalchemy import text

from alembic import op
from app.database import SCHEMA

revision = "20260815_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in SCHEMA.split(";"):
        statement = statement.strip()
        if statement and not statement.upper().startswith("PRAGMA"):
            op.execute(text(statement))


def downgrade() -> None:
    for table in (
        "jobs",
        "knowledge_point_dependencies",
        "mastery_evidence",
        "remediations",
        "attempts",
        "questions",
        "learning_steps",
        "debts",
        "knowledge_points",
        "reconstructions",
        "document_chunks",
        "transcript_segments",
        "resources",
        "sessions",
        "courses",
    ):
        op.execute(text(f"DROP TABLE IF EXISTS {table}"))
