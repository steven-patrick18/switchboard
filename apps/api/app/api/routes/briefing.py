import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db import get_db
from app.intake import evaluate as evaluate_intake
from app.models import (
    AgentRun,
    Approval,
    Client,
    ClientIntake,
    Document,
    Project,
    Task,
    User,
)
from app.models.approval import DECISION_PENDING
from app.schemas.briefing import BriefingClient, BriefingOut, BriefingTotals

router = APIRouter(prefix="/briefing", tags=["briefing"])

_OPEN_TASK_STATUSES = {"queued", "running", "awaiting_approval"}


@router.get("", response_model=BriefingOut)
async def daily_briefing(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BriefingOut:
    """Operator-wide morning digest across all owned clients. Deterministic
    (no LLM): what progressed, what is blocked, what is waiting on you."""
    clients = list(
        (
            await db.scalars(
                select(Client)
                .where(Client.owner_id == user.id)
                .order_by(Client.created_at)
            )
        ).all()
    )
    client_ids = [c.id for c in clients]

    # Per-client intake completeness.
    intakes: dict[uuid.UUID, ClientIntake] = {}
    doc_types: dict[uuid.UUID, set[str]] = defaultdict(set)
    if client_ids:
        for ci in (
            await db.scalars(
                select(ClientIntake).where(ClientIntake.client_id.in_(client_ids))
            )
        ).all():
            intakes[ci.client_id] = ci
        for cid, dtype in (
            await db.execute(
                select(Document.client_id, Document.type).where(
                    Document.client_id.in_(client_ids)
                )
            )
        ).all():
            doc_types[cid].add(dtype)

    # Operator-scoped task / approval / agent-run rows.
    task_rows = (
        await db.execute(
            select(Task.status, Project.client_id)
            .join(Project, Project.id == Task.project_id)
            .join(Client, Client.id == Project.client_id)
            .where(Client.owner_id == user.id)
        )
    ).all()
    approval_rows = (
        await db.execute(
            select(Approval.decision, Project.client_id)
            .join(Task, Task.id == Approval.task_id)
            .join(Project, Project.id == Task.project_id)
            .join(Client, Client.id == Project.client_id)
            .where(Client.owner_id == user.id)
        )
    ).all()
    cost_rows = (
        await db.execute(
            select(AgentRun.cost)
            .join(Task, Task.id == AgentRun.task_id)
            .join(Project, Project.id == Task.project_id)
            .join(Client, Client.id == Project.client_id)
            .where(Client.owner_id == user.id)
        )
    ).all()

    tasks_by_status = Counter(s for s, _ in task_rows)
    open_by_client: Counter = Counter(
        cid for s, cid in task_rows if s in _OPEN_TASK_STATUSES
    )
    pending_by_client: Counter = Counter(
        cid for d, cid in approval_rows if d == DECISION_PENDING
    )
    total_pending = sum(pending_by_client.values())
    total_cost = round(float(sum(c or 0 for (c,) in cost_rows)), 4)

    client_summaries: list[BriefingClient] = []
    blocked: list[str] = []
    for c in clients:
        complete = evaluate_intake(
            intakes.get(c.id), doc_types.get(c.id, set())
        ).complete
        if not complete:
            blocked.append(c.name)
        client_summaries.append(
            BriefingClient(
                client_id=c.id,
                name=c.name,
                stage=c.stage,
                intake_complete=complete,
                pending_approvals=pending_by_client.get(c.id, 0),
                open_tasks=open_by_client.get(c.id, 0),
            )
        )

    attention: list[str] = []
    if total_pending:
        attention.append(
            f"{total_pending} approval(s) waiting on you in the queue."
        )
    if blocked:
        attention.append(
            f"Intake incomplete (blocked): {', '.join(blocked)}."
        )
    awaiting = tasks_by_status.get("awaiting_approval", 0)
    if awaiting:
        attention.append(f"{awaiting} task(s) paused awaiting approval.")
    if not attention:
        attention.append("Nothing requires your attention.")

    n = len(clients)
    complete_n = sum(1 for cs in client_summaries if cs.intake_complete)
    summary = (
        f"{n} client(s): {complete_n} fully captured, {n - complete_n} with "
        f"incomplete intake. {total_pending} approval(s) waiting. "
        f"{tasks_by_status.get('running', 0)} task(s) running, {awaiting} "
        f"awaiting approval, {tasks_by_status.get('completed', 0)} completed. "
        f"{len(cost_rows)} agent run(s), ${total_cost} spent."
    )

    return BriefingOut(
        generated_at=datetime.now(UTC),
        summary=summary,
        totals=BriefingTotals(
            clients=n,
            pending_approvals=total_pending,
            agent_runs=len(cost_rows),
            total_cost=total_cost,
            tasks_by_status=dict(tasks_by_status),
        ),
        attention=attention,
        clients=client_summaries,
    )
