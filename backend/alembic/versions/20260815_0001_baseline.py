"""Create the KnowledgeDebt 0.1 application schema."""

from __future__ import annotations

import json

import sqlalchemy as sa
from sqlalchemy import inspect, text

from alembic import op
from app.database import SCHEMA
from app.models import DEFAULT_PROFILE, utc_now

revision = "20260815_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in SCHEMA.split(";"):
        statement = statement.strip()
        if statement and not statement.upper().startswith("PRAGMA"):
            op.execute(text(statement))

    bind = op.get_bind()

    def add_missing(table: str, columns: dict[str, sa.Column]) -> None:
        existing = {column["name"] for column in inspect(bind).get_columns(table)}
        for name, column in columns.items():
            if name not in existing:
                op.add_column(table, column)

    add_missing(
        "resources",
        {
            "start_offset": sa.Column("start_offset", sa.Float(), nullable=True),
            "end_offset": sa.Column("end_offset", sa.Float(), nullable=True),
            "session_duration": sa.Column("session_duration", sa.Float(), nullable=True),
            "capture_range_json": sa.Column(
                "capture_range_json", sa.Text(), nullable=False, server_default="[]"
            ),
            "storage_provider": sa.Column(
                "storage_provider", sa.Text(), nullable=False, server_default="local"
            ),
            "storage_key": sa.Column("storage_key", sa.Text(), nullable=True),
        },
    )
    add_missing(
        "transcript_segments",
        {
            "global_start": sa.Column("global_start", sa.Float(), nullable=True),
            "global_end": sa.Column("global_end", sa.Float(), nullable=True),
        },
    )
    op.execute(
        text(
            """UPDATE transcript_segments
               SET global_start = start_time + COALESCE(
                     (SELECT start_offset FROM resources
                      WHERE resources.id = transcript_segments.resource_id), 0
                   ),
                   global_end = end_time + COALESCE(
                     (SELECT start_offset FROM resources
                      WHERE resources.id = transcript_segments.resource_id), 0
                   )
               WHERE global_start IS NULL OR global_end IS NULL"""
        )
    )
    add_missing(
        "questions",
        {
            "knowledge_point_ids_json": sa.Column(
                "knowledge_point_ids_json", sa.Text(), nullable=False, server_default="[]"
            ),
            "question_type": sa.Column(
                "question_type", sa.Text(), nullable=False, server_default="diagnostic"
            ),
            "parent_question_id": sa.Column("parent_question_id", sa.Text(), nullable=True),
        },
    )
    for row in bind.execute(
        text("SELECT id, knowledge_point_id FROM questions WHERE knowledge_point_ids_json='[]'")
    ).mappings():
        bind.execute(
            text("UPDATE questions SET knowledge_point_ids_json=:point_ids WHERE id=:id"),
            {"point_ids": json.dumps([row["knowledge_point_id"]]), "id": row["id"]},
        )
    for row in bind.execute(text("SELECT id, profile_json FROM courses")).mappings():
        try:
            profile = json.loads(row["profile_json"])
        except (TypeError, json.JSONDecodeError):
            profile = {}
        if not set(DEFAULT_PROFILE).issubset(profile):
            bind.execute(
                text("UPDATE courses SET profile_json=:profile, updated_at=:updated_at WHERE id=:id"),
                {
                    "profile": json.dumps(DEFAULT_PROFILE),
                    "updated_at": utc_now(),
                    "id": row["id"],
                },
            )


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
