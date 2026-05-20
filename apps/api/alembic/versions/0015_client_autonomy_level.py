"""client autonomy level

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-20

Adds per-client autonomy level. Default 'supervised' keeps every
tier-2/tier-3 action gated on operator approval (the original
invariant). 'autonomous' makes tier-2 actions auto-execute (still
audited; an Approval row is created with decision='approved' for
forensic traceability). Tier-3 (filings under penalty of perjury,
e-signatures) stays gated regardless of mode — that's the legal
floor.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column(
            "autonomy_level",
            sa.String(length=32),
            nullable=False,
            server_default="supervised",
        ),
    )


def downgrade() -> None:
    op.drop_column("clients", "autonomy_level")
