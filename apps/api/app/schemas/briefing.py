import uuid
from datetime import datetime

from pydantic import BaseModel


class BriefingClient(BaseModel):
    client_id: uuid.UUID
    name: str
    stage: str
    intake_complete: bool
    pending_approvals: int
    open_tasks: int


class BriefingTotals(BaseModel):
    clients: int
    pending_approvals: int
    agent_runs: int
    total_cost: float
    tasks_by_status: dict[str, int]


class BriefingOut(BaseModel):
    generated_at: datetime
    summary: str
    totals: BriefingTotals
    attention: list[str]
    clients: list[BriefingClient]
