"""Operator-wide live activity feed: what every agent is doing right now,
which assignments are currently claimed, and the last ~30 completed runs
across all of the operator's clients. Used by the GUI's `/activity` page
which polls this every few seconds. Read-only; operator-scoped."""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.applications import spec_for
from app.db import get_db
from app.models import AgentRun, Application, Client, Project, Task, User

router = APIRouter(prefix="/activity", tags=["activity"])

# Tasks the GUI calls "in flight" — work the operator still has eyes on.
_RUNNING = "running"
_AWAITING = "awaiting_approval"
# Application stages that mean an agent has actively claimed the row.
_ACTIVE_STAGES = {"in_progress", "awaiting_approval", "blocked"}


@router.get("")
async def activity(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Live view of agent activity across all of this operator's clients.
    Three feeds:
      - running_now: tasks currently mid-flight inside an agent loop
      - awaiting_approval: tasks paused with one or more queued approvals
      - current_assignments: applications an agent has claimed (set
        current_agent + stage != not_started/complete)
      - recent_runs: the 30 most recent agent runs, with cost/tokens
    """
    # Client lookup map — every row in every feed gets a client_name.
    clients = list(
        (
            await db.scalars(
                select(Client).where(Client.owner_id == user.id)
            )
        ).all()
    )
    name_by_client: dict[uuid.UUID, str] = {c.id: c.name for c in clients}
    client_id_by_project: dict[uuid.UUID, uuid.UUID] = {}
    if clients:
        rows = (
            await db.execute(
                select(Project.id, Project.client_id).where(
                    Project.client_id.in_(name_by_client.keys())
                )
            )
        ).all()
        client_id_by_project = {pid: cid for pid, cid in rows}

    # ---- running_now + awaiting_approval -------------------------------
    in_flight_rows = (
        await db.scalars(
            select(Task)
            .join(Project, Project.id == Task.project_id)
            .join(Client, Client.id == Project.client_id)
            .where(
                Client.owner_id == user.id,
                Task.status.in_([_RUNNING, _AWAITING]),
            )
            .order_by(Task.created_at.desc())
        )
    ).all()

    def _task_payload(t: Task) -> dict[str, object]:
        cid = client_id_by_project.get(t.project_id)
        instruction = ""
        if isinstance(t.input, dict):
            instruction = str(t.input.get("instruction") or t.input.get("objective") or "")
        return {
            "task_id": str(t.id),
            "agent": t.agent,
            "client_id": str(cid) if cid else None,
            "client_name": name_by_client.get(cid, "—") if cid else "—",
            "instruction": instruction[:240],
            "status": t.status,
            "started_at": t.created_at.isoformat() if t.created_at else None,
        }

    running_now = [_task_payload(t) for t in in_flight_rows if t.status == _RUNNING]
    awaiting_approval = [
        _task_payload(t) for t in in_flight_rows if t.status == _AWAITING
    ]

    # ---- current_assignments (Applications agents have claimed) --------
    app_rows: list[Application] = []
    if name_by_client:
        app_rows = list(
            (
                await db.scalars(
                    select(Application)
                    .where(
                        Application.client_id.in_(name_by_client.keys()),
                        Application.current_agent.is_not(None),
                        Application.stage.in_(_ACTIVE_STAGES),
                    )
                    .order_by(Application.updated_at.desc())
                )
            ).all()
        )
    current_assignments = []
    for a in app_rows:
        spec = spec_for(a.type)
        current_assignments.append(
            {
                "client_id": str(a.client_id),
                "client_name": name_by_client.get(a.client_id, "—"),
                "application_type": a.type,
                "application_label": spec.label if spec else a.type,
                "stage": a.stage,
                "current_agent": a.current_agent,
                "notes": (a.notes or "")[:240] or None,
                "updated_at": a.updated_at.isoformat() if a.updated_at else None,
            }
        )

    # ---- recent_runs (30 most recent AgentRuns, operator-scoped) -------
    run_rows = list(
        (
            await db.execute(
                select(AgentRun, Task, Project.client_id)
                .join(Task, Task.id == AgentRun.task_id, isouter=True)
                .join(Project, Project.id == Task.project_id, isouter=True)
                .join(Client, Client.id == Project.client_id, isouter=True)
                .where(Client.owner_id == user.id)
                .order_by(AgentRun.created_at.desc())
                .limit(30)
            )
        ).all()
    )
    recent_runs = []
    for run, task, cid in run_rows:
        instruction = ""
        if task is not None and isinstance(task.input, dict):
            instruction = str(
                task.input.get("instruction") or task.input.get("objective") or ""
            )
        recent_runs.append(
            {
                "run_id": str(run.id),
                "agent": run.agent,
                "task_id": str(run.task_id) if run.task_id else None,
                "task_status": task.status if task is not None else None,
                "client_id": str(cid) if cid else None,
                "client_name": name_by_client.get(cid, "—") if cid else "—",
                "instruction": instruction[:240],
                "started_at": run.created_at.isoformat() if run.created_at else None,
                "duration_ms": run.duration_ms or 0,
                "tokens_in": run.tokens_in or 0,
                "tokens_out": run.tokens_out or 0,
                "cost": float(run.cost or 0),
            }
        )

    # ---- totals (last 24h cost/tokens) ---------------------------------
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    last_24h = (
        await db.execute(
            select(AgentRun.cost, AgentRun.tokens_in, AgentRun.tokens_out)
            .join(Task, Task.id == AgentRun.task_id, isouter=True)
            .join(Project, Project.id == Task.project_id, isouter=True)
            .join(Client, Client.id == Project.client_id, isouter=True)
            .where(
                Client.owner_id == user.id,
                AgentRun.created_at >= cutoff,
            )
        )
    ).all()
    cost_24h = round(float(sum((c or 0) for c, _, _ in last_24h)), 4)
    tokens_24h = sum((ti or 0) + (to or 0) for _, ti, to in last_24h)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "running_now": running_now,
        "awaiting_approval": awaiting_approval,
        "current_assignments": current_assignments,
        "recent_runs": recent_runs,
        "totals": {
            "running": len(running_now),
            "awaiting_approval": len(awaiting_approval),
            "assignments_active": len(current_assignments),
            "runs_last_24h": len(last_24h),
            "tokens_last_24h": tokens_24h,
            "cost_last_24h": cost_24h,
        },
    }
