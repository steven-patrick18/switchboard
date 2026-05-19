import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Document(Base):
    """A filing, contract, or certificate — versioned, DocuSign-ready."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    # Content-addressed storage key (= SHA-256 of the bytes). Documents
    # registered without an uploaded file are metadata-only stubs (e.g.,
    # an agent's output marker) and have these unset.
    s3_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    docusign_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    @property
    def has_file(self) -> bool:
        return self.s3_key is not None and (self.size_bytes or 0) > 0
