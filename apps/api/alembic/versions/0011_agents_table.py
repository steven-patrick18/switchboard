"""operator-defined agents

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-20

Stores agents created from the GUI. Tools stay code-defined in
app/agents/tools.py; an agent row references them by name. Owner-
scoped — different operators can have agents with the same name
without collision.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column(
            "tool_names",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("effort", sa.String(length=16), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("owner_id", "name", name="uq_agent_owner_name"),
    )


def downgrade() -> None:
    op.drop_table("agents")
