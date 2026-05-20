"""platform settings (GUI-editable runtime config)

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-20

Single-row-per-key table for runtime config the operator edits via
the GUI (ANTHROPIC_API_KEY, SMTP host/from/...) — replaces the need
to drop into .env to change anything.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_settings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "key",
            sa.String(length=64),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("platform_settings")
