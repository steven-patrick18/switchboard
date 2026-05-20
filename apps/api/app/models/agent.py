"""Operator-defined AI agents stored in the database.

The built-in agents (pm / compliance / document) are code-defined in
app/agents/*.py; this table holds operator-created agents composed via
the GUI. The operator picks from the code-defined tool catalog (each
tool's tier-classification is the load-bearing safety property — that
stays in code). Per-operator uniqueness on name lets one operator
override a built-in by re-using its name without affecting others.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_agent_owner_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    system_prompt: Mapped[str] = mapped_column(Text)
    # List of tool names referencing app/agents/tools.py exports. Stored
    # as JSON so SQLite can hold it without a join table.
    tool_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Null = inherit settings.agent_model / agent_effort.
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effort: Mapped[str | None] = mapped_column(String(16), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
