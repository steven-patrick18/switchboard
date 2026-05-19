import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import run_agent
from app.agents.registry import get_agent
from app.api.deps import get_current_user
from app.db import get_db
from app.llm import get_anthropic_client
from app.models import Client, Project, Task, User
from app.schemas.agents import AgentRunResponse
from app.schemas.tasks import TaskOut

router = APIRouter(prefix="/clients/{client_id}", tags=["tasks"])

TASK_STATUS_QUEUED = "queued"


async def _owned_client(
    client_id: uuid.UUID, user: User, db: AsyncSession
) -> Client:
    client = await db.get(Client, client_id)
    if client is None or client.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Client not found"
        )
    return client


@router.get("/tasks", response_model=list[TaskOut])
async def list_tasks(
    client_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Task]:
    await _owned_client(client_id, user, db)
    result = await db.scalars(
        select(Task)
        .join(Project, Project.id == Task.project_id)
        .where(Project.client_id == client_id)
        .order_by(Task.created_at)
    )
    return list(result)


@router.post("/tasks/{task_id}/run", response_model=AgentRunResponse)
async def run_task(
    client_id: uuid.UUID,
    task_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    anthropic=Depends(get_anthropic_client),
) -> AgentRunResponse:
    """Run a queued sub-task (e.g. one PM delegated) through its agent.
    Reuses the runtime + approval gateway — anything the sub-agent wants
    to do externally still lands in the approval queue."""
    await _owned_client(client_id, user, db)

    task = await db.get(Task, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )
    project = await db.get(Project, task.project_id)
    if project is None or project.client_id != client_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )
    if task.status != TASK_STATUS_QUEUED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task is not queued (status={task.status})",
        )

    spec = get_agent(task.agent)
    if spec is None or task.agent == "pm":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Task agent '{task.agent}' is not runnable here.",
        )
    objective = (task.input or {}).get("objective")
    if not objective:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Task has no objective to run.",
        )

    task.status = "running"
    await db.flush()
    result = await run_agent(
        spec,
        client=anthropic,
        db=db,
        task_id=task.id,
        instruction=objective,
        client_id=client_id,
        project_id=project.id,
    )
    task.status = "awaiting_approval" if result.approval_ids else "completed"
    task.output = {"text": result.text}
    await db.commit()

    return AgentRunResponse(
        agent=spec.name,
        task_id=task.id,
        text=result.text,
        approval_ids=result.approval_ids,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost=round(result.cost, 4),
        duration_ms=result.duration_ms,
        iterations=result.iterations,
    )
