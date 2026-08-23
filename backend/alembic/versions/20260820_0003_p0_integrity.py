"""Add durable recordings, authoritative schedule snapshots and application settings."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260820_0003"
down_revision = "20260815_0002"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    tables = _tables()
    if "app_settings" not in tables:
        op.create_table(
            "app_settings",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("timezone", sa.Text(), nullable=False, server_default="Asia/Shanghai"),
            sa.Column("auto_transcribe", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
            sa.CheckConstraint("id='default'", name="ck_app_settings_singleton"),
        )
    if "schedule_sync_batches" not in tables:
        op.create_table(
            "schedule_sync_batches",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("connector", sa.Text(), nullable=False),
            sa.Column("source", sa.Text(), nullable=False),
            sa.Column("academic_term", sa.Text(), nullable=False),
            sa.Column("term_id", sa.Text(), sa.ForeignKey("academic_terms.id", ondelete="SET NULL")),
            sa.Column("snapshot_hash", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("diff_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("error", sa.Text()),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("applied_at", sa.Text()),
            sa.UniqueConstraint(
                "connector", "academic_term", "snapshot_hash", name="uq_schedule_snapshot"
            ),
        )
    for table in ("schedule_rules", "schedule_occurrences"):
        columns = _columns(table)
        if "source" not in columns:
            op.add_column(table, sa.Column("source", sa.Text(), nullable=False, server_default="manual"))
        if "last_seen_batch_id" not in columns:
            op.add_column(table, sa.Column("last_seen_batch_id", sa.Text()))
        if "sync_status" not in columns:
            op.add_column(table, sa.Column("sync_status", sa.Text(), nullable=False, server_default="active"))
    if "recordings" not in tables:
        op.create_table(
            "recordings",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("session_id", sa.Text(), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.Text(), nullable=False, server_default="recording"),
            sa.Column("mime_type", sa.Text(), nullable=False),
            sa.Column("filename", sa.Text(), nullable=False),
            sa.Column("start_offset", sa.Float(), nullable=False, server_default="0"),
            sa.Column("session_duration", sa.Float()),
            sa.Column("duration_seconds", sa.Float()),
            sa.Column("last_sequence", sa.Integer()),
            sa.Column("auto_transcribe", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("resource_id", sa.Text(), sa.ForeignKey("resources.id", ondelete="SET NULL"), unique=True),
            sa.Column("failure_reason", sa.Text()),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
            sa.Column("completed_at", sa.Text()),
        )
    if "recording_chunks" not in tables:
        op.create_table(
            "recording_chunks",
            sa.Column("recording_id", sa.Text(), sa.ForeignKey("recordings.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("sequence", sa.Integer(), primary_key=True),
            sa.Column("checksum", sa.Text(), nullable=False),
            sa.Column("byte_size", sa.Integer(), nullable=False),
            sa.Column("mime_type", sa.Text(), nullable=False),
            sa.Column("local_path", sa.Text(), nullable=False),
            sa.Column("received_at", sa.Text(), nullable=False),
        )
    if "snoozed_until" not in _columns("review_items"):
        op.add_column("review_items", sa.Column("snoozed_until", sa.Text()))

    # Old append-only imports may have reused an external ID after a time change.
    # Preserve every historical row, but move non-canonical duplicates to a traceable legacy identity.
    op.execute(
        sa.text(
            """WITH ranked AS (
                 SELECT id, ROW_NUMBER() OVER (
                   PARTITION BY rule_id, external_id
                   ORDER BY CASE WHEN EXISTS (
                     SELECT 1 FROM session_automation sa WHERE sa.occurrence_id=schedule_occurrences.id
                   ) THEN 0 ELSE 1 END, created_at
                 ) AS duplicate_rank
                 FROM schedule_occurrences
               )
               UPDATE schedule_occurrences
               SET external_id=external_id || ':legacy:' || substr(id, 1, 8),
                   sync_status='superseded'
               WHERE id IN (SELECT id FROM ranked WHERE duplicate_rank>1)"""
        )
    )
    op.execute(
        sa.text(
            """WITH ranked AS (
                 SELECT id, ROW_NUMBER() OVER (
                   PARTITION BY storage_provider, storage_key ORDER BY created_at
                 ) AS duplicate_rank
                 FROM inbox_items
               )
               UPDATE inbox_items
               SET storage_key=storage_key || ':legacy:' || substr(id, 1, 8),
                   matching_status='rejected', archived=1
               WHERE id IN (SELECT id FROM ranked WHERE duplicate_rank>1)"""
        )
    )
    op.execute(
        sa.text(
            """WITH ranked AS (
                 SELECT id, ROW_NUMBER() OVER (
                   PARTITION BY kind, subject_type, subject_id ORDER BY created_at
                 ) AS duplicate_rank
                 FROM review_items WHERE status IN ('pending', 'later')
               )
               UPDATE review_items SET status='rejected',
                 decision_reason='数据库升级时合并重复待审核项', decided_at=updated_at
               WHERE id IN (SELECT id FROM ranked WHERE duplicate_rank>1)"""
        )
    )
    if "idx_occurrence_external_identity" not in _indexes("schedule_occurrences"):
        op.create_index(
            "idx_occurrence_external_identity",
            "schedule_occurrences",
            ["rule_id", "external_id"],
            unique=True,
        )
    if "idx_recordings_session_status" not in _indexes("recordings"):
        op.create_index(
            "idx_recordings_session_status", "recordings", ["session_id", "status", "updated_at"]
        )
    if "idx_inbox_storage_identity" not in _indexes("inbox_items"):
        op.create_index(
            "idx_inbox_storage_identity",
            "inbox_items",
            ["storage_provider", "storage_key"],
            unique=True,
        )
    if "idx_one_open_review_per_subject" not in _indexes("review_items"):
        op.create_index(
            "idx_one_open_review_per_subject",
            "review_items",
            ["kind", "subject_type", "subject_id"],
            unique=True,
            sqlite_where=sa.text("status IN ('pending', 'later')"),
            postgresql_where=sa.text("status IN ('pending', 'later')"),
        )


def downgrade() -> None:
    for table, name in (
        ("review_items", "idx_one_open_review_per_subject"),
        ("inbox_items", "idx_inbox_storage_identity"),
        ("schedule_occurrences", "idx_occurrence_external_identity"),
    ):
        if name in _indexes(table):
            op.drop_index(name, table_name=table)
    if "snoozed_until" in _columns("review_items"):
        op.drop_column("review_items", "snoozed_until")
    for table in ("recording_chunks", "recordings", "schedule_sync_batches", "app_settings"):
        if table in _tables():
            op.drop_table(table)
    for table in ("schedule_occurrences", "schedule_rules"):
        for column in ("sync_status", "last_seen_batch_id", "source"):
            if column in _columns(table):
                op.drop_column(table, column)
