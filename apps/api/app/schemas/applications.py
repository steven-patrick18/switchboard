import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    client_id: uuid.UUID
    type: str
    label: str
    description: str
    default_agent: str
    stage: str
    current_agent: str | None
    notes: str | None
    external_ref: str | None
    created_at: datetime
    updated_at: datetime


class ApplicationCreate(BaseModel):
    """Manual single-application add (e.g. operator adds carrier:bandwidth)."""

    type: str = Field(min_length=1, max_length=64)
    notes: str | None = Field(default=None, max_length=4000)


class ApplicationUpdate(BaseModel):
    """All fields optional. Send what you want to change."""

    stage: str | None = Field(default=None, max_length=32)
    current_agent: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=4000)
    external_ref: str | None = Field(default=None, max_length=128)


class ApplicationResolveResult(BaseModel):
    """Returned from 'sync from intake' — what was created vs what was
    already there. The endpoint is idempotent: re-running it adds any
    newly-required applications without duplicating existing ones."""

    created: list[str]
    existing: list[str]
