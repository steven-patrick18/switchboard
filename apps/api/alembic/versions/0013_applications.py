"""per-filing applications + stage tracker

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-20

Captures the lifecycle of each regulated filing (OCN, FCC 499, RMD,
STIR/SHAKEN, Section 214, per-state CPCNs, per-carrier interconnects)
so the operator and the agents share a single source of truth on
"where is X right now".
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "applications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("client_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("type", sa.String(length=64), nullable=False, index=True),
        sa.Column(
            "stage",
            sa.String(length=32),
            nullable=False,
            server_default="not_started",
        ),
        sa.Column("current_agent", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("external_ref", sa.String(length=128), nullable=True),
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
    )


def downgrade() -> None:
    op.drop_table("applications")
