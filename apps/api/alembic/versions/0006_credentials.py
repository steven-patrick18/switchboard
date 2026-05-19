"""per-client credential vault

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-20

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "credentials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "client_id",
            sa.Uuid(),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("service", sa.String(64), nullable=False),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("secret_ciphertext", sa.String(4000), nullable=False),
        sa.Column("scope", sa.String(255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "client_id", "service", name="uq_credential_client_service"
        ),
    )
    op.create_index(
        "ix_credentials_client_id", "credentials", ["client_id"]
    )


def downgrade() -> None:
    op.drop_table("credentials")
