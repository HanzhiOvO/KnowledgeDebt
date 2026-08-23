"""Add non-sensitive custom headers for OpenAI-compatible profiles."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260821_0006"
down_revision = "20260821_0005"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "custom_headers_json" not in _columns("provider_profiles"):
        op.add_column(
            "provider_profiles",
            sa.Column(
                "custom_headers_json",
                sa.Text(),
                nullable=False,
                server_default="{}",
            ),
        )


def downgrade() -> None:
    if "custom_headers_json" in _columns("provider_profiles"):
        op.drop_column("provider_profiles", "custom_headers_json")
