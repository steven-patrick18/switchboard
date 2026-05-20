"""Operator corrections captured as in-context lessons for an agent.

Every time the operator rejects an approval with a reason, or edits an
approval's payload before approving it, a row goes in here keyed to the
agent that queued the action. At run time, the agent's system prompt is
prepended with a short list of recent lessons addressed to it. This is
supervised in-context learning: the same Claude model, smarter agent
behavior, no fine-tuning.

Owner-scoped so one operator's corrections don't bleed into another
operator's runs.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# Where the lesson came from. Mostly informational on the UI but also
# lets the operator filter "show me only the rejection lessons".
SOURCE_REJECTION = "rejection"
SOURCE_EDIT = "edit"
SOURCE_MANUAL = "manual"


class AgentLesson(Base):
    __tablename__ = "agent_lessons"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Which agent the lesson is for. Stored as a name (not FK) so a
    # built-in agent ("compliance") and a custom override of the same
    # name both consume the same lesson stream.
    agent_name: Mapped[str] = mapped_column(String(64), index=True)
    # rejection | edit | manual
    source: Mapped[str] = mapped_column(String(16))
    # Tier-specific context: the action that triggered the correction,
    # nullable for manually-added lessons.
    action_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Free-text lesson — what the agent should remember next time.
    # This is what gets injected into the system prompt.
    lesson: Mapped[str] = mapped_column(Text)
    # Optional pointer back to the approval that triggered this lesson,
    # so the operator can dig into the original context.
    source_approval_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
