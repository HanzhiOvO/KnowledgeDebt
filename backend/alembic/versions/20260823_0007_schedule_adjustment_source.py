"""Preserve the source identity of effective schedule adjustments."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260823_0007"
down_revision = "20260821_0006"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "adjustment_external_id" not in _columns("schedule_occurrences"):
        op.add_column(
            "schedule_occurrences",
            sa.Column("adjustment_external_id", sa.Text()),
        )


def downgrade() -> None:
    if "adjustment_external_id" in _columns("schedule_occurrences"):
        op.drop_column("schedule_occurrences", "adjustment_external_id")
