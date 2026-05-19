import uuid

from pydantic import BaseModel, Field


class ComplianceRunRequest(BaseModel):
    client_id: uuid.UUID
    instruction: str = Field(min_length=1, max_length=8000)


class ComplianceRunResponse(BaseModel):
    task_id: uuid.UUID
    text: str
    approval_ids: list[uuid.UUID]
    tokens_in: int
    tokens_out: int
    cost: float
    duration_ms: int
    iterations: int
