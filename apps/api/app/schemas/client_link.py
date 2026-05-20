import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ClientLinkCreate(BaseModel):
    label: str | None = Field(default=None, max_length=128)
    # Hours from now until expiration; None = never expires (operator
    # can still revoke explicitly). Capped at one year.
    expires_in_hours: int | None = Field(default=None, ge=1, le=24 * 365)


class ClientLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    client_id: uuid.UUID
    label: str | None
    # Token is included in the create response so the operator can copy
    # the URL once; subsequent list calls also include it (operator can
    # always see what they generated).
    token: str
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class ClientLinkUsage(BaseModel):
    """What the client sees on the public portal: the bare minimum to
    identify which company they're filling intake for, nothing else."""

    client_name: str
    stage: str
    label: str | None
