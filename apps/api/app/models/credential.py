import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Credential(Base):
    """A per-client external login (FCC CORES, IRS, state PUC, bank,
    carrier portal...). The secret is stored ENCRYPTED and is only ever
    decrypted server-side — never returned through the API or placed in
    agent/model context. One row per (client, service)."""

    __tablename__ = "credentials"
    __table_args__ = (
        UniqueConstraint("client_id", "service", name="uq_credential_client_service"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    service: Mapped[str] = mapped_column(String(64))
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    secret_ciphertext: Mapped[str] = mapped_column(String(4000))
    scope: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
