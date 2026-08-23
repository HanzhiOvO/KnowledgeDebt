"""Track MediaRecorder stream boundaries for safe resume and packaging."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260821_0004"
down_revision = "20260820_0003"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    columns = _columns("recording_chunks")
    if "stream_id" not in columns:
        op.add_column(
            "recording_chunks",
            sa.Column("stream_id", sa.Text(), nullable=False, server_default="legacy"),
        )
    if "stream_index" not in columns:
        op.add_column(
            "recording_chunks",
            sa.Column("stream_index", sa.Integer(), nullable=False, server_default="0"),
        )
    if "idx_recording_chunks_stream" not in _indexes("recording_chunks"):
        op.create_index(
            "idx_recording_chunks_stream",
            "recording_chunks",
            ["recording_id", "stream_index", "sequence"],
        )


def downgrade() -> None:
    if "idx_recording_chunks_stream" in _indexes("recording_chunks"):
        op.drop_index("idx_recording_chunks_stream", table_name="recording_chunks")
    columns = _columns("recording_chunks")
    if "stream_index" in columns:
        op.drop_column("recording_chunks", "stream_index")
    if "stream_id" in columns:
        op.drop_column("recording_chunks", "stream_id")
