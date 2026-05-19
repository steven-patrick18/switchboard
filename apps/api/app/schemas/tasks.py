import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.agents import AgentRunResponse


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    agent: str
    status: str
    input: dict | None
    output: dict | None
    created_at: datetime


class SkippedTask(BaseModel):
    task_id: uuid.UUID
    reason: str


class BulkRunResult(BaseModel):
    ran: list[AgentRunResponse]
    failed: list[SkippedTask]
    skipped: list[SkippedTask]
    capped: bool
