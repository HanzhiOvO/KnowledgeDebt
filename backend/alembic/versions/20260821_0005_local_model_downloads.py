"""Persist resumable local Whisper model downloads."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260821_0005"
down_revision = "20260821_0004"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if "local_model_downloads" not in _tables():
        op.create_table(
            "local_model_downloads",
            sa.Column("model_id", sa.Text(), primary_key=True),
            sa.Column("status", sa.Text(), nullable=False, server_default="not_downloaded"),
            sa.Column("file_name", sa.Text(), nullable=False),
            sa.Column("expected_sha256", sa.Text(), nullable=False),
            sa.Column("total_bytes", sa.BigInteger(), nullable=False),
            sa.Column("bytes_downloaded", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("error", sa.Text()),
            sa.Column("verified_size", sa.BigInteger()),
            sa.Column("verified_mtime_ns", sa.Text()),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
            sa.Column("completed_at", sa.Text()),
        )
    if "idx_local_model_download_status" not in _indexes("local_model_downloads"):
        op.create_index(
            "idx_local_model_download_status",
            "local_model_downloads",
            ["status", "updated_at"],
        )


def downgrade() -> None:
    if "local_model_downloads" not in _tables():
        return
    if "idx_local_model_download_status" in _indexes("local_model_downloads"):
        op.drop_index("idx_local_model_download_status", table_name="local_model_downloads")
    op.drop_table("local_model_downloads")
