import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# Launch stages are playbook-driven and evolve per win — kept as a free
# string rather than a DB enum so playbook edits don't require a migration.
CLIENT_STAGE_INTAKE = "intake"

# Autonomy levels: 'supervised' = original behavior, every T2/T3 queues
# for operator approval. 'autonomous' = T2 actions auto-execute (still
# audited via an Approval row with decision='approved'); T3 always
# queues regardless — that's the legal floor (filings under penalty of
# perjury, e-signatures).
AUTONOMY_SUPERVISED = "supervised"
AUTONOMY_AUTONOMOUS = "autonomous"
AUTONOMY_LEVELS = {AUTONOMY_SUPERVISED, AUTONOMY_AUTONOMOUS}


class Client(Base):
    """An isolated client workspace owned by one operator."""

    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    ein: Mapped[str | None] = mapped_column(String(20), nullable=True)
    state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    stage: Mapped[str] = mapped_column(String(64), default=CLIENT_STAGE_INTAKE)
    playbook_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    autonomy_level: Mapped[str] = mapped_column(
        String(32), default=AUTONOMY_SUPERVISED, server_default=AUTONOMY_SUPERVISED
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
