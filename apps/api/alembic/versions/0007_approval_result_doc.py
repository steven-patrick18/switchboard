"""approval result document link

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-20

The column is added without a DB-level FK constraint so the migration
applies cleanly on SQLite (batch_alter_table cannot copy unnamed FK
constraints from older revisions). The SQLAlchemy model still declares
the ForeignKey for ORM-level wiring and for create_all in tests; a
named DB constraint can be added separately when moving to Postgres
in prod.

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "approvals", sa.Column("result_document_id", sa.Uuid(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("approvals", "result_document_id")
