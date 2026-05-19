import uuid

from pydantic import BaseModel, Field


class AgentRunRequest(BaseModel):
    client_id: uuid.UUID
    instruction: str = Field(min_length=1, max_length=8000)


class AgentRunResponse(BaseModel):
    agent: str
    task_id: uuid.UUID
    text: str
    approval_ids: list[uuid.UUID]
    tokens_in: int
    tokens_out: int
    cost: float
    duration_ms: int
    iterations: int
