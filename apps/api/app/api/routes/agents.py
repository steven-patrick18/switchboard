import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentSpec, run_agent
from app.agents.registry import AGENTS, get_agent
from app.agents.tools import ALL_TOOLS, get_tool
from app.api.deps import get_current_user
from app.audit import record_audit
from app.db import get_db
from app.llm import get_anthropic_client
from app.models import Agent as AgentRow
from app.models import AgentLesson, Client, Project, Task, User
from app.models.agent_lesson import SOURCE_MANUAL
from app.models.project import PROJECT_STATUS_ACTIVE
from app.schemas.agent_crud import (
    AgentCreate,
    AgentOut,
    AgentUpdate,
    LessonCreate,
    LessonOut,
    ToolCatalogEntry,
)
from app.schemas.agents import AgentRunRequest, AgentRunResponse

router = APIRouter(prefix="/agents", tags=["agents"])


# ---------- Tool catalog --------------------------------------------------


@router.get("/tools", response_model=list[ToolCatalogEntry])
async def list_tools(_: User = Depends(get_current_user)) -> list[ToolCatalogEntry]:
    """Every tool the operator can pick when composing an agent. Tier
    classifications are code-defined and shown in the UI as a colored
    pill so the operator sees which tools auto-run vs. queue an
    approval. Tools cannot be created from the GUI."""
    return [
        ToolCatalogEntry(name=t.name, description=t.description, tier=t.tier)
        for t in sorted(ALL_TOOLS.values(), key=lambda t: (t.tier, t.name))
    ]


# ---------- CRUD ----------------------------------------------------------


def _builtin_to_out(spec: AgentSpec) -> AgentOut:
    return AgentOut(
        id=None,
        name=spec.name,
        description=None,
        system_prompt=spec.system_prompt,
        tool_names=[t.name for t in spec.tools],
        model=spec.model,
        effort=None,
        enabled=True,
        is_builtin=True,
        created_at=None,
        updated_at=None,
    )


def _custom_to_out(row: AgentRow) -> AgentOut:
    return AgentOut(
        id=row.id,
        name=row.name,
        description=row.description,
        system_prompt=row.system_prompt,
        tool_names=row.tool_names or [],
        model=row.model,
        effort=row.effort,
        enabled=row.enabled,
        is_builtin=False,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("", response_model=list[AgentOut])
async def list_all_agents(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AgentOut]:
    """Built-in code-defined agents + this operator's custom agents.
    An operator's custom agent that re-uses a built-in name takes
    precedence at run time but BOTH are listed here so the UI can show
    `(override)` on the custom one. Each row carries lesson_count, the
    number of operator corrections the agent has been taught — the UI
    surfaces this as a 'learned: N' chip."""
    rows = list(
        (
            await db.scalars(
                select(AgentRow)
                .where(AgentRow.owner_id == user.id)
                .order_by(AgentRow.name)
            )
        ).all()
    )
    # One query for all lesson counts per agent_name for this operator.
    counts_rows = (
        await db.execute(
            select(AgentLesson.agent_name, func.count(AgentLesson.id))
            .where(AgentLesson.owner_id == user.id)
            .group_by(AgentLesson.agent_name)
        )
    ).all()
    counts: dict[str, int] = {name: int(c) for name, c in counts_rows}
    builtins = []
    for s in AGENTS.values():
        out = _builtin_to_out(s)
        out.lesson_count = counts.get(s.name, 0)
        builtins.append(out)
    custom = []
    for r in rows:
        out = _custom_to_out(r)
        out.lesson_count = counts.get(r.name, 0)
        custom.append(out)
    return builtins + custom


# ---------- Lessons (the learning system) ---------------------------


@router.get("/{agent_name}/lessons", response_model=list[LessonOut])
async def list_lessons(
    agent_name: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AgentLesson]:
    """All lessons the operator has taught this agent. Newest first."""
    rows = await db.scalars(
        select(AgentLesson)
        .where(
            AgentLesson.owner_id == user.id,
            AgentLesson.agent_name == agent_name,
        )
        .order_by(AgentLesson.created_at.desc())
    )
    return list(rows.all())


@router.post(
    "/{agent_name}/lessons",
    response_model=LessonOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_manual_lesson(
    agent_name: str,
    body: LessonCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentLesson:
    """Operator can hand-write a lesson — e.g. 'always use the legal
    company name on FCC filings, not the DBA'. Same in-context injection
    as auto-captured ones."""
    row = AgentLesson(
        owner_id=user.id,
        agent_name=agent_name,
        source=SOURCE_MANUAL,
        lesson=body.lesson,
    )
    db.add(row)
    await db.flush()
    await record_audit(
        db,
        actor=user.email,
        action="agent_lesson.added",
        subject=f"agent_lesson:{row.id}",
        after={"agent_name": agent_name},
    )
    await db.commit()
    await db.refresh(row)
    return row


@router.delete(
    "/lessons/{lesson_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_lesson(
    lesson_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a lesson. Useful when the operator's standards change or
    the captured reason was situational rather than general."""
    row = await db.get(AgentLesson, lesson_id)
    if row is None or row.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lesson not found"
        )
    agent_name = row.agent_name
    await db.delete(row)
    await record_audit(
        db,
        actor=user.email,
        action="agent_lesson.deleted",
        subject=f"agent_lesson:{lesson_id}",
        after={"agent_name": agent_name},
    )
    await db.commit()


def _validate_tool_names(tool_names: list[str]) -> None:
    unknown = [n for n in tool_names if get_tool(n) is None]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Unknown tool name(s): "
                + ", ".join(unknown)
                + ". GET /agents/tools to see available tools."
            ),
        )


@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AgentCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    _validate_tool_names(body.tool_names)
    row = AgentRow(
        owner_id=user.id,
        name=body.name,
        description=body.description,
        system_prompt=body.system_prompt,
        tool_names=body.tool_names,
        model=body.model,
        effort=body.effort,
        enabled=body.enabled,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You already have an agent named '{body.name}'.",
        ) from None
    await record_audit(
        db,
        actor=user.email,
        action="agent.created",
        subject=f"agent:{row.id}",
        after={
            "name": row.name,
            "tool_names": list(row.tool_names or []),
            "model": row.model,
        },
    )
    await db.commit()
    await db.refresh(row)
    return _custom_to_out(row)


async def _load_owned_agent(
    agent_id: uuid.UUID, user: User, db: AsyncSession
) -> AgentRow:
    row = await db.get(AgentRow, agent_id)
    if row is None or row.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found"
        )
    return row


@router.put("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    row = await _load_owned_agent(agent_id, user, db)
    payload = body.model_dump(exclude_unset=True)
    if "tool_names" in payload and payload["tool_names"] is not None:
        _validate_tool_names(payload["tool_names"])
    changed: list[str] = []
    for k, v in payload.items():
        if getattr(row, k) != v:
            changed.append(k)
        setattr(row, k, v)
    row.updated_at = datetime.now(UTC)
    if changed:
        await record_audit(
            db,
            actor=user.email,
            action="agent.updated",
            subject=f"agent:{row.id}",
            after={"fields_changed": changed},
        )
    await db.commit()
    await db.refresh(row)
    return _custom_to_out(row)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    row = await _load_owned_agent(agent_id, user, db)
    name = row.name
    await db.delete(row)
    await record_audit(
        db,
        actor=user.email,
        action="agent.deleted",
        subject=f"agent:{agent_id}",
        after={"name": name},
    )
    await db.commit()


# ---------- Resolve + run ------------------------------------------------


async def _resolve_runnable(
    name: str, user: User, db: AsyncSession
) -> AgentSpec | None:
    """Custom (DB) agent takes precedence over the built-in of the same
    name. Disabled custom agents are skipped (fall through to built-in
    if one exists; otherwise None)."""
    row = await db.scalar(
        select(AgentRow).where(
            AgentRow.owner_id == user.id,
            AgentRow.name == name,
            AgentRow.enabled.is_(True),
        )
    )
    if row is not None:
        tools = [get_tool(n) for n in (row.tool_names or [])]
        return AgentSpec(
            name=row.name,
            system_prompt=row.system_prompt,
            tools=[t for t in tools if t is not None],
            model=row.model,
        )
    return get_agent(name)


@router.post("/{agent_name}/run", response_model=AgentRunResponse)
async def run(
    agent_name: str,
    body: AgentRunRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    anthropic=Depends(get_anthropic_client),
) -> AgentRunResponse:
    spec = await _resolve_runnable(agent_name, user, db)
    if spec is None:
        available = sorted(set(AGENTS.keys()))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown or disabled agent '{agent_name}'. Built-ins: {available}",
        )

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
        agent=spec.name,
        status="running",
        input={"instruction": body.instruction},
    )
    db.add(task)
    await db.flush()
    task_id: uuid.UUID = task.id

    result = await run_agent(
        spec,
        client=anthropic,
        db=db,
        task_id=task_id,
        instruction=body.instruction,
        client_id=client_row.id,
        project_id=project.id,
        owner_id=user.id,
    )

    task.status = "awaiting_approval" if result.approval_ids else "completed"
    task.output = {"text": result.text}
    await db.commit()

    return AgentRunResponse(
        agent=spec.name,
        task_id=task_id,
        text=result.text,
        approval_ids=result.approval_ids,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost=round(result.cost, 4),
        duration_ms=result.duration_ms,
        iterations=result.iterations,
    )
