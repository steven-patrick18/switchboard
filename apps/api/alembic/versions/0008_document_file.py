"""document file fields (filename, mime, size_bytes)

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-20

Adds the columns that record the uploaded bytes alongside the
existing `s3_key` (= content-addressed SHA-256 storage key).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents", sa.Column("filename", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("mime", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("size_bytes", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("documents", "size_bytes")
    op.drop_column("documents", "mime")
    op.drop_column("documents", "filename")
