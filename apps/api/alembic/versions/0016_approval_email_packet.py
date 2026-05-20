"""approval email packet

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-20

Adds an email_packet JSON column on approvals so each approved filing
carries the prefilled email (to/from/subject/body) the operator needs
to send. Also adds email_sent_at + email_message_id so we can record
the outbound send (and later match inbound replies back by
Message-ID, when IMAP/webhook integration lands).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("approvals", sa.Column("email_packet", sa.JSON(), nullable=True))
    op.add_column(
        "approvals",
        sa.Column("email_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "approvals",
        sa.Column("email_message_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("approvals", "email_message_id")
    op.drop_column("approvals", "email_sent_at")
    op.drop_column("approvals", "email_packet")
