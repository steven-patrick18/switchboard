"""agent lessons (captured operator corrections)

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-20

Stores the corrections an operator makes against an agent's queued
work — rejection reasons and payload edits — so the agent can be
re-prompted with them on the next run. Owner-scoped; an agent's
lessons are private to the operator that taught them.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_lessons",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False, index=True),
        sa.Column(
            "agent_name", sa.String(length=64), nullable=False, index=True
        ),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=True),
        sa.Column("lesson", sa.Text(), nullable=False),
        sa.Column("source_approval_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            index=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("agent_lessons")
