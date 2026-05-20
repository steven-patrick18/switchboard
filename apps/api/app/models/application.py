"""Per-filing stage tracker.

Every regulated launch has many parallel "applications" — OCN, FCC 499,
RMD, STIR/SHAKEN, Section 214 (if international), state CPCNs (one per
target state), wholesale carrier interconnections. This table is the
operator's single source of truth for where each one is.

Stages are deliberately coarse — the goal is glance-able status, not
fine-grained workflow. Sub-detail lives in Tasks, Approvals, and audit.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# Stages — append-only. Adding new ones is fine; renaming/removing
# requires a migration of existing rows.
STAGE_NOT_STARTED = "not_started"
STAGE_IN_PROGRESS = "in_progress"      # An agent is actively drafting
STAGE_AWAITING_APPROVAL = "awaiting_approval"  # In the operator's queue
STAGE_SUBMITTED = "submitted"           # Filed with the external party
STAGE_UNDER_REVIEW = "under_review"     # Waiting on FCC / state / carrier
STAGE_COMPLETE = "complete"
STAGE_BLOCKED = "blocked"               # Hit an issue — operator attention

ALL_STAGES = {
    STAGE_NOT_STARTED,
    STAGE_IN_PROGRESS,
    STAGE_AWAITING_APPROVAL,
    STAGE_SUBMITTED,
    STAGE_UNDER_REVIEW,
    STAGE_COMPLETE,
    STAGE_BLOCKED,
}

# Open = "needs work". Used for at-a-glance counters and for filtering.
OPEN_STAGES = {
    STAGE_NOT_STARTED,
    STAGE_IN_PROGRESS,
    STAGE_AWAITING_APPROVAL,
    STAGE_UNDER_REVIEW,
    STAGE_BLOCKED,
}


class Application(Base):
    """A single filing / process in the client's launch.

    `type` is a string like 'ocn', 'fcc_499', 'rmd', 'stir_shaken',
    'section_214', 'state_cpcn:TX', 'carrier:twilio'. Resolution from
    intake → application list lives in app/applications.py.
    """

    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    # Short machine code (e.g. 'fcc_499'). Human label comes from
    # app/applications.py::label_for(type).
    type: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[str] = mapped_column(String(32), default=STAGE_NOT_STARTED)
    # Which agent is currently on the file (set by agents themselves via
    # the update_application_stage tool, or by the operator manually).
    current_agent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Free-text note the agent / operator can drop on the row, e.g.
    # "Waiting on RMD — usually 5-10 business days."
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # External reference number once filed (FCC filer ID, state docket
    # number, NECA OCN code, etc.) — populated by the operator after
    # the external system returns one.
    external_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
