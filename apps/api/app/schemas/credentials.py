import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CredentialUpsert(BaseModel):
    """Create-or-replace a credential. Always carries a secret.
    Operators use this when adding a new credential; the platform
    encrypts the secret server-side before storing."""

    service: str = Field(min_length=1, max_length=64)
    secret: str = Field(min_length=1, max_length=4000)
    username: str | None = Field(default=None, max_length=255)
    url: str | None = Field(default=None, max_length=512)
    scope: str | None = Field(default=None, max_length=255)
    expires_at: datetime | None = None


class CredentialPatch(BaseModel):
    """Edit metadata on an existing credential without rotating the
    secret. To rotate, pass `new_secret`. All fields optional — only
    the ones present in the request body are touched."""

    username: str | None = Field(default=None, max_length=255)
    url: str | None = Field(default=None, max_length=512)
    scope: str | None = Field(default=None, max_length=255)
    expires_at: datetime | None = None
    new_secret: str | None = Field(default=None, min_length=1, max_length=4000)


class CredentialOut(BaseModel):
    """Metadata only — the secret is write-only and never serialized."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    service: str
    username: str | None
    url: str | None
    scope: str | None
    expires_at: datetime | None
    last_accessed_at: datetime | None
    created_at: datetime
