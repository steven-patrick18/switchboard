import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ApprovalOut(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    client_id: uuid.UUID
    client_name: str
    action_type: str
    tier: str
    payload: dict | None
    decision: str
    reviewer_id: uuid.UUID | None
    note: str | None
    ts: datetime


class ApproveBody(BaseModel):
    # Provide to edit-and-approve: replaces the queued payload and records
    # the decision as "edited".
    payload_override: dict | None = None
    note: str | None = Field(default=None, max_length=2000)


class RejectBody(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class BatchBody(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    decision: Literal["approved", "rejected"]
    note: str | None = Field(default=None, max_length=2000)


class BatchResult(BaseModel):
    updated: list[uuid.UUID]
    skipped: list[uuid.UUID]
