import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import run_agent
from app.agents.compliance import COMPLIANCE_AGENT
from app.api.deps import get_current_user
from app.config import settings
from app.db import get_db
from app.models import Client, Project, Task, User
from app.models.project import PROJECT_STATUS_ACTIVE
from app.schemas.agents import ComplianceRunRequest, ComplianceRunResponse

router = APIRouter(prefix="/agents", tags=["agents"])


def _anthropic_client():
    if not settings.anthropic_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ANTHROPIC_API_KEY is not configured.",
        )
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(api_key=settings.anthropic_api_key)


@router.post("/compliance/run", response_model=ComplianceRunResponse)
async def run_compliance(
    body: ComplianceRunRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ComplianceRunResponse:
    client_row = await db.get(Client, body.client_id)
    if client_row is None or client_row.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Client not found"
        )

    project = await db.scalar(
        select(Project).where(Project.client_id == client_row.id)
    )
    if project is None:
        project = Project(client_id=client_row.id, status=PROJECT_STATUS_ACTIVE)
        db.add(project)
        await db.flush()

    task = Task(
        project_id=project.id,
        agent=COMPLIANCE_AGENT.name,
        status="running",
        input={"instruction": body.instruction},
    )
    db.add(task)
    await db.flush()
    task_id: uuid.UUID = task.id

    anthropic = _anthropic_client()
    result = await run_agent(
        COMPLIANCE_AGENT,
        client=anthropic,
        db=db,
        task_id=task_id,
        instruction=body.instruction,
    )

    task.status = "awaiting_approval" if result.approval_ids else "completed"
    task.output = {"text": result.text}
    await db.commit()

    return ComplianceRunResponse(
        task_id=task_id,
        text=result.text,
        approval_ids=result.approval_ids,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost=round(result.cost, 4),
        duration_ms=result.duration_ms,
        iterations=result.iterations,
    )
