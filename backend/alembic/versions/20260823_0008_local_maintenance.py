"""Add safe recording chunk retention metadata."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260823_0008"
down_revision = "20260823_0007"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    app_columns = _columns("app_settings")
    if "recording_chunk_retention_days" not in app_columns:
        op.add_column(
            "app_settings",
            sa.Column(
                "recording_chunk_retention_days",
                sa.Integer(),
                nullable=True,
                server_default="14",
            ),
        )
    recording_columns = _columns("recordings")
    for column in (
        sa.Column("raw_chunks_verified_at", sa.Text()),
        sa.Column("raw_chunks_purged_at", sa.Text()),
        sa.Column("raw_chunks_purged_bytes", sa.BigInteger()),
    ):
        if column.name not in recording_columns:
            op.add_column("recordings", column)


def downgrade() -> None:
    recording_columns = _columns("recordings")
    for name in ("raw_chunks_purged_bytes", "raw_chunks_purged_at", "raw_chunks_verified_at"):
        if name in recording_columns:
            op.drop_column("recordings", name)
    if "recording_chunk_retention_days" in _columns("app_settings"):
        op.drop_column("app_settings", "recording_chunk_retention_days")
