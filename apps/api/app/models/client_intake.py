import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ClientIntake(Base):
    """Captured-once intake for a client. Every datum the whole VoIP launch
    needs is gathered here at onboarding so agents auto-fill filings and the
    client is never re-disturbed mid-process (the brief's form-field memory)."""

    __tablename__ = "client_intakes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), unique=True, index=True
    )

    legal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    formation_state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    ein: Mapped[str | None] = mapped_column(String(20), nullable=True)
    principal_address: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    officer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    officer_title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    officer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    primary_contact_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_contact_email: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    primary_contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    target_states: Mapped[list | None] = mapped_column(JSON, nullable=True)
    intends_international: Mapped[bool] = mapped_column(Boolean, default=False)
    estimated_monthly_revenue: Mapped[float | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    ocn: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Playbook-driven extra fields captured at intake (extensible).
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
