"""approval execution record

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-20

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "approvals",
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "approvals", sa.Column("execution_result", sa.String(2000), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("approvals", "execution_result")
    op.drop_column("approvals", "executed_at")
