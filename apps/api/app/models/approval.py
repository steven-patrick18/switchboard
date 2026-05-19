import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# The approval queue is the product. Every external/regulated action
# lands here before it can execute.
DECISION_PENDING = "pending"
DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"
DECISION_EDITED = "edited"

# Action tiers from the safety architecture.
TIER_AUTO = "T0"
TIER_AUTO_NOTIFY = "T1"
TIER_APPROVE = "T2"
TIER_SIGN_PAY = "T3"


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    action_type: Mapped[str] = mapped_column(String(64))
    tier: Mapped[str] = mapped_column(String(4), default=TIER_APPROVE)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decision: Mapped[str] = mapped_column(String(16), default=DECISION_PENDING)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Reviewer's reason on reject, or note on edit&approve.
    note: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Set when an approved action's in-platform follow-through has run.
    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    execution_result: Mapped[str | None] = mapped_column(
        String(2000), nullable=True
    )
    # Set when the executor materializes a Document Hub artifact, so the
    # history view can deep-link to it.
    result_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
