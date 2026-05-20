"""client share links table

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-20

Magic-link access for the public client portal. One client can have
many links (different labels, different expirations). The token is the
shared secret; uniqueness is indexed so lookup is O(1).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Indexes are declared inline so Alembic emits them as part of the
    # CREATE TABLE — re-declaring them with op.create_index afterwards
    # triggers "index already exists" on SQLite (column-level index=True
    # auto-creates the index in the same statement).
    op.create_table(
        "client_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("client_id", sa.Uuid(), nullable=False, index=True),
        sa.Column(
            "token",
            sa.String(length=128),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("label", sa.String(length=128), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("client_links")
