"""credential url column

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-20

Adds an optional URL field to each credential so the agent / operator
knows which login page the credential is for. Especially needed for
custom carrier portals and domain webmail where the URL isn't
obvious from the service name alone.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "credentials",
        sa.Column("url", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("credentials", "url")
