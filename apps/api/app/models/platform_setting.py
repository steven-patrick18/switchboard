"""Platform-wide runtime settings that need to be editable through the
GUI rather than the .env file.

One row per key. Secret values (ANTHROPIC_API_KEY, SMTP password) are
stored encrypted using the existing Fernet wrapper in app/crypto.py;
non-secret values (SMTP host, public URL) are stored plaintext. The
get_anthropic_client and notifications modules read from this table
first, falling back to the env config when the DB row is empty — so
existing .env-based deploys keep working.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PlatformSetting(Base):
    __tablename__ = "platform_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When True, `value` is a Fernet ciphertext and must be decrypted
    # before use (and NEVER returned through the API).
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
