import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentSpec, run_agent
from app.agents.registry import get_agent
from app.api.deps import get_current_user
from app.config import settings
from app.db import get_db
from app.llm import get_anthropic_client
from app.models import Client, Project, Task, User
from app.schemas.agents import AgentRunResponse
from app.schemas.tasks import BulkRunResult, SkippedTask, TaskOut

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


def _resolve_runnable(
    task: Task,
) -> tuple[AgentSpec | None, str | None, str | None]:
    """(spec, objective, None) if runnable, else (None, None, reason)."""
    spec = get_agent(task.agent)
    if spec is None or task.agent == "pm":
        return None, None, f"agent '{task.agent}' is not runnable"
    objective = (task.input or {}).get("objective")
    if not objective:
        return None, None, "task has no objective"
    return spec, objective, None


async def _execute(
    task: Task,
    project: Project,
    spec: AgentSpec,
    objective: str,
    client_id: uuid.UUID,
    anthropic,
    db: AsyncSession,
) -> AgentRunResponse:
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

    spec, objective, reason = _resolve_runnable(task)
    if reason:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=reason.capitalize()
        )

    return await _execute(
        task, project, spec, objective, client_id, anthropic, db
    )


@router.post("/tasks/run-queued", response_model=BulkRunResult)
async def run_queued(
    client_id: uuid.UUID,
    limit: int | None = Query(default=None, ge=1),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    anthropic=Depends(get_anthropic_client),
) -> BulkRunResult:
    """Sweep a client's queued sub-tasks (as PM delegates them). Bounded
    by `bulk_run_max` to cap API spend; each task is isolated so one
    failure doesn't abort the sweep. Every sub-agent's external action
    still flows through the approval gateway."""
    await _owned_client(client_id, user, db)

    cap = settings.bulk_run_max
    effective = cap if limit is None else min(limit, cap)

    task_ids = list(
        (
            await db.scalars(
                select(Task.id)
                .join(Project, Project.id == Task.project_id)
                .where(
                    Project.client_id == client_id,
                    Task.status == TASK_STATUS_QUEUED,
                )
                .order_by(Task.created_at)
            )
        ).all()
    )

    ran: list[AgentRunResponse] = []
    failed: list[SkippedTask] = []
    skipped: list[SkippedTask] = []
    capped = False

    for tid in task_ids:
        task = await db.get(Task, tid)
        if task is None or task.status != TASK_STATUS_QUEUED:
            continue
        spec, objective, reason = _resolve_runnable(task)
        if reason:
            skipped.append(SkippedTask(task_id=tid, reason=reason))
            continue
        if len(ran) >= effective:
            capped = True
            skipped.append(
                SkippedTask(task_id=tid, reason=f"cap reached (limit={effective})")
            )
            continue
        project = await db.get(Project, task.project_id)
        try:
            ran.append(
                await _execute(
                    task, project, spec, objective, client_id, anthropic, db
                )
            )
        except Exception as e:  # noqa: BLE001 — boundary: external LLM call
            await db.rollback()
            t2 = await db.get(Task, tid)
            if t2 is not None:
                t2.status = "failed"
                t2.output = {"error": str(e)[:500]}
                await db.commit()
            failed.append(
                SkippedTask(task_id=tid, reason=f"error: {str(e)[:200]}")
            )

    return BulkRunResult(
        ran=ran, failed=failed, skipped=skipped, capped=capped
    )
