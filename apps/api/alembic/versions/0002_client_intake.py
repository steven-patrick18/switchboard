"""client intake (capture-once form-field memory)

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-20

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "client_intakes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "client_id",
            sa.Uuid(),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("legal_name", sa.String(255), nullable=True),
        sa.Column("entity_type", sa.String(64), nullable=True),
        sa.Column("formation_state", sa.String(2), nullable=True),
        sa.Column("ein", sa.String(20), nullable=True),
        sa.Column("principal_address", sa.JSON(), nullable=True),
        sa.Column("officer_name", sa.String(255), nullable=True),
        sa.Column("officer_title", sa.String(128), nullable=True),
        sa.Column("officer_email", sa.String(255), nullable=True),
        sa.Column("primary_contact_name", sa.String(255), nullable=True),
        sa.Column("primary_contact_email", sa.String(255), nullable=True),
        sa.Column("primary_contact_phone", sa.String(32), nullable=True),
        sa.Column("target_states", sa.JSON(), nullable=True),
        sa.Column(
            "intends_international",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("estimated_monthly_revenue", sa.Numeric(14, 2), nullable=True),
        sa.Column("ocn", sa.String(16), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_client_intakes_client_id",
        "client_intakes",
        ["client_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("client_intakes")
