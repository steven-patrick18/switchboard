"""Magic-link access to one client's onboarding surface.

The operator generates a link, sends the URL to the client (email,
text, signal — anything). The client opens it and can: fill the
intake form, upload required documents, and drop in portal/carrier
credentials — *for that one client only*. Everything they do is
audited with actor=`client:{link_id}` so the operator's trail shows
exactly who did what.

A link can be revoked or set to auto-expire. The token itself is a
high-entropy random string stored verbatim (we look up by it on every
request and a leaked link is automatically scoped to one client).
"""

import secrets
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def generate_token() -> str:
    # 32-byte URL-safe token → ~43 chars. Plenty of entropy; un-guessable.
    return secrets.token_urlsafe(32)


class ClientLink(Base):
    __tablename__ = "client_links"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    token: Mapped[str] = mapped_column(
        String(128), unique=True, index=True, default=generate_token
    )
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
