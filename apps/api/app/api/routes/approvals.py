import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_learning import record_edit_lesson, record_rejection_lesson
from app.api.deps import get_current_user
from app.audit import record_audit
from app.db import get_db
from app.execution import execute_approval
from app.models import Approval, Client, Project, Task, User
from app.models.approval import (
    DECISION_APPROVED,
    DECISION_EDITED,
    DECISION_PENDING,
    DECISION_REJECTED,
)
from app.schemas.approvals import (
    ApprovalOut,
    ApproveBody,
    BatchBody,
    BatchResult,
    RejectBody,
    SendBackBody,
    SendBackResult,
)

router = APIRouter(prefix="/approvals", tags=["approvals"])

# Approval -> Task -> Project -> Client; the operator owns the Client.
_OWNED = (
    select(Approval, Client)
    .join(Task, Task.id == Approval.task_id)
    .join(Project, Project.id == Task.project_id)
    .join(Client, Client.id == Project.client_id)
)


def _out(approval: Approval, client: Client) -> ApprovalOut:
    return ApprovalOut(
        id=approval.id,
        task_id=approval.task_id,
        client_id=client.id,
        client_name=client.name,
        action_type=approval.action_type,
        tier=approval.tier,
        payload=approval.payload,
        decision=approval.decision,
        reviewer_id=approval.reviewer_id,
        note=approval.note,
        executed_at=approval.executed_at,
        execution_result=approval.execution_result,
        result_document_id=approval.result_document_id,
        ts=approval.ts,
    )


async def _load_owned(
    approval_id: uuid.UUID, user: User, db: AsyncSession
) -> tuple[Approval, Client]:
    row = (
        await db.execute(
            _OWNED.where(Approval.id == approval_id, Client.owner_id == user.id)
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found"
        )
    return row[0], row[1]


def _require_pending(approval: Approval) -> None:
    if approval.decision != DECISION_PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Approval already {approval.decision}",
        )


@router.get("/count")
async def pending_count(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """How many approvals are waiting on the operator right now. Light
    endpoint so the sidebar badge can poll cheaply."""
    n = await db.scalar(
        select(func.count(Approval.id))
        .join(Task, Task.id == Approval.task_id)
        .join(Project, Project.id == Task.project_id)
        .join(Client, Client.id == Project.client_id)
        .where(
            Client.owner_id == user.id, Approval.decision == DECISION_PENDING
        )
    )
    return {"pending": int(n or 0)}


@router.get("", response_model=list[ApprovalOut])
async def list_approvals(
    decision: str = Query(default=DECISION_PENDING),
    client_id: uuid.UUID | None = Query(default=None),
    action_type: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApprovalOut]:
    stmt = _OWNED.where(Client.owner_id == user.id)
    # `decided` is the history view's meta-value: everything not pending.
    if decision == "decided":
        stmt = stmt.where(Approval.decision != DECISION_PENDING)
    elif decision != "all":
        stmt = stmt.where(Approval.decision == decision)
    if client_id is not None:
        stmt = stmt.where(Client.id == client_id)
    if action_type is not None:
        stmt = stmt.where(Approval.action_type == action_type)
    rows = (
        await db.execute(stmt.order_by(Approval.ts.desc()).limit(limit))
    ).all()
    return [_out(a, c) for a, c in rows]


@router.get("/{approval_id}", response_model=ApprovalOut)
async def get_approval(
    approval_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApprovalOut:
    approval, client = await _load_owned(approval_id, user, db)
    return _out(approval, client)


async def _agent_name_for_approval(approval: Approval, db: AsyncSession) -> str | None:
    """Look up which agent originally queued this approval, by joining
    through its Task. Returns None for orphan approvals (shouldn't
    happen in practice but the learning path stays safe either way)."""
    task = await db.get(Task, approval.task_id)
    return task.agent if task is not None else None


async def _close_task_if_done(task_id: uuid.UUID, db: AsyncSession) -> None:
    """Move the parent task to 'completed' once every approval it
    queued has been decided. This is what was missing — without it
    a task stayed at 'awaiting_approval' forever even after the
    operator decided every approval, so the Live Activity feed kept
    showing it as in-flight. We only flip from 'awaiting_approval'
    (not from 'running', 'queued', or anything else) to avoid racing
    with an agent that's still mid-loop."""
    task = await db.get(Task, task_id)
    if task is None or task.status != "awaiting_approval":
        return
    still_pending = await db.scalar(
        select(func.count(Approval.id)).where(
            Approval.task_id == task_id,
            Approval.decision == DECISION_PENDING,
        )
    )
    if not still_pending:
        task.status = "completed"


@router.post("/{approval_id}/approve", response_model=ApprovalOut)
async def approve(
    approval_id: uuid.UUID,
    body: ApproveBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApprovalOut:
    approval, client = await _load_owned(approval_id, user, db)
    _require_pending(approval)
    before_payload = approval.payload
    edited = body.payload_override is not None
    if edited:
        approval.payload = body.payload_override
        approval.decision = DECISION_EDITED
    else:
        approval.decision = DECISION_APPROVED
    approval.reviewer_id = user.id
    approval.note = body.note
    approval.ts = datetime.now(UTC)
    await record_audit(
        db,
        actor=user.email,
        action=f"approval.{approval.decision}",
        subject=f"approval:{approval.id}",
        client_id=client.id,
        before={"payload": before_payload}
        if approval.decision == DECISION_EDITED
        else None,
        after={
            "decision": approval.decision,
            "payload": approval.payload,
            "note": approval.note,
        },
    )
    # Operator edited before approving → teach the agent the corrected shape.
    if edited:
        agent_name = await _agent_name_for_approval(approval, db)
        if agent_name:
            await record_edit_lesson(
                db,
                owner_id=user.id,
                agent_name=agent_name,
                action_type=approval.action_type,
                note=body.note,
                before_payload=before_payload,
                after_payload=approval.payload,
                approval_id=approval.id,
            )
    approval.execution_result = await execute_approval(approval, client.id, db)
    approval.executed_at = datetime.now(UTC)
    await record_audit(
        db,
        actor="system",
        action="approval.executed",
        subject=f"approval:{approval.id}",
        client_id=client.id,
        after={"result": approval.execution_result},
    )
    await _close_task_if_done(approval.task_id, db)
    await db.commit()
    return _out(approval, client)


@router.post("/{approval_id}/reject", response_model=ApprovalOut)
async def reject(
    approval_id: uuid.UUID,
    body: RejectBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApprovalOut:
    approval, client = await _load_owned(approval_id, user, db)
    _require_pending(approval)
    approval.decision = DECISION_REJECTED
    approval.reviewer_id = user.id
    approval.note = body.reason
    approval.ts = datetime.now(UTC)
    await record_audit(
        db,
        actor=user.email,
        action="approval.rejected",
        subject=f"approval:{approval.id}",
        client_id=client.id,
        after={"decision": DECISION_REJECTED, "note": body.reason},
    )
    # Teach the agent: the reason is the lesson.
    agent_name = await _agent_name_for_approval(approval, db)
    if agent_name:
        await record_rejection_lesson(
            db,
            owner_id=user.id,
            agent_name=agent_name,
            action_type=approval.action_type,
            reason=body.reason,
            approval_id=approval.id,
        )
    await _close_task_if_done(approval.task_id, db)
    await db.commit()
    return _out(approval, client)


@router.post("/{approval_id}/send-back", response_model=SendBackResult)
async def send_back(
    approval_id: uuid.UUID,
    body: SendBackBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SendBackResult:
    """One-click correction loop: this approval is wrong; rerun the
    same agent with the operator's feedback so it produces an updated
    draft. Concretely:

      1. The pending approval is rejected with `feedback` as the
         reason — which captures it as an AgentLesson so the agent
         learns from the correction across future runs too.
      2. A new task is created on the same project with the same
         agent, and an instruction that combines the original
         objective + the operator's correction.
      3. If an Anthropic key is configured we run the agent in-line
         and return the new approval id(s). If not (or the run
         errors out), the new task stays queued — the operator can
         hit Start on it later.
    """
    approval, client = await _load_owned(approval_id, user, db)
    _require_pending(approval)
    old_task = await db.get(Task, approval.task_id)
    if old_task is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Original task missing; cannot send back.",
        )
    agent_name = old_task.agent
    old_input = old_task.input or {}
    original = str(
        old_input.get("instruction") or old_input.get("objective") or ""
    ).strip()

    # Step 1 — reject the pending approval with the feedback as the
    # captured reason. This is exactly the same path the explicit
    # Reject button takes, so the lesson + audit semantics are
    # identical.
    approval.decision = DECISION_REJECTED
    approval.reviewer_id = user.id
    approval.note = body.feedback
    approval.ts = datetime.now(UTC)
    await record_audit(
        db,
        actor=user.email,
        action="approval.sent_back",
        subject=f"approval:{approval.id}",
        client_id=client.id,
        after={"decision": DECISION_REJECTED, "feedback": body.feedback},
    )
    if agent_name:
        await record_rejection_lesson(
            db,
            owner_id=user.id,
            agent_name=agent_name,
            action_type=approval.action_type,
            reason=body.feedback,
            approval_id=approval.id,
        )
    await _close_task_if_done(approval.task_id, db)

    # Step 2 — build a new task that tells the agent what to redo.
    project_id = old_task.project_id
    new_instruction = (
        (original or f"Re-do {approval.action_type}.")
        + "\n\nOPERATOR FEEDBACK on your previous draft "
        + f"(approval {approval.id}): "
        + body.feedback.strip()
        + "\n\nProduce an updated draft that incorporates the operator's "
        + "feedback above and queue it again."
    )
    new_task = Task(
        project_id=project_id,
        agent=agent_name or "pm",
        status="queued",
        input={"instruction": new_instruction, "sent_back_from": str(approval.id)},
    )
    db.add(new_task)
    await db.flush()
    new_task_id = new_task.id

    # Step 3 — best-effort auto-run. We gracefully degrade if the
    # Anthropic key isn't configured or the run itself errors. Worst
    # case the task stays queued and the operator hits Start.
    ran = False
    new_approval_ids: list[uuid.UUID] = []
    agent_reply: str | None = None
    try:
        from anthropic import AsyncAnthropic  # noqa: PLC0415

        from app import platform_config  # noqa: PLC0415
        from app.agents.base import run_agent  # noqa: PLC0415
        from app.api.routes.agents import _resolve_runnable  # noqa: PLC0415

        key = await platform_config.anthropic_api_key(db)
        if key:
            spec = await _resolve_runnable(agent_name, user, db) if agent_name else None
            if spec is not None:
                anthro = AsyncAnthropic(api_key=key)
                new_task.status = "running"
                await db.flush()
                result = await run_agent(
                    spec,
                    client=anthro,
                    db=db,
                    task_id=new_task_id,
                    instruction=new_instruction,
                    client_id=client.id,
                    project_id=project_id,
                    owner_id=user.id,
                    autonomy_level=client.autonomy_level,
                )
                new_task.status = (
                    "awaiting_approval" if result.approval_ids else "completed"
                )
                new_task.output = {"text": result.text}
                new_approval_ids = list(result.approval_ids)
                agent_reply = result.text
                ran = True
    except Exception as exc:  # noqa: BLE001 — external LLM boundary
        # The task is already created (queued); the operator can
        # retry it via Start ▸ . Capture the error so it isn't silent.
        new_task.status = "queued"
        new_task.output = {"send_back_run_error": str(exc)[:500]}

    await db.commit()
    return SendBackResult(
        rejected_approval_id=approval_id,
        new_task_id=new_task_id,
        ran=ran,
        new_approval_ids=new_approval_ids,
        agent_reply=agent_reply,
    )


@router.post("/batch", response_model=BatchResult)
async def batch(
    body: BatchBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BatchResult:
    updated: list[uuid.UUID] = []
    skipped: list[uuid.UUID] = []
    for aid in body.ids:
        row = (
            await db.execute(
                _OWNED.where(Approval.id == aid, Client.owner_id == user.id)
            )
        ).first()
        if row is None or row[0].decision != DECISION_PENDING:
            skipped.append(aid)
            continue
        approval = row[0]
        approval.decision = body.decision
        approval.reviewer_id = user.id
        approval.note = body.note
        approval.ts = datetime.now(UTC)
        await record_audit(
            db,
            actor=user.email,
            action=f"approval.{body.decision}",
            subject=f"approval:{approval.id}",
            client_id=row[1].id,
            after={"decision": body.decision, "note": body.note},
        )
        if body.decision == DECISION_APPROVED:
            approval.execution_result = await execute_approval(
                approval, row[1].id, db
            )
            approval.executed_at = datetime.now(UTC)
            await record_audit(
                db,
                actor="system",
                action="approval.executed",
                subject=f"approval:{approval.id}",
                client_id=row[1].id,
                after={"result": approval.execution_result},
            )
        elif body.decision == DECISION_REJECTED:
            # Same reason captured for every rejected approval in the
            # batch — each agent that contributed work gets the lesson.
            agent_name = await _agent_name_for_approval(approval, db)
            if agent_name and body.note:
                await record_rejection_lesson(
                    db,
                    owner_id=user.id,
                    agent_name=agent_name,
                    action_type=approval.action_type,
                    reason=body.note,
                    approval_id=approval.id,
                )
        updated.append(aid)
    # Close out any task whose last pending approval was just decided.
    closed_tasks: set[uuid.UUID] = set()
    for aid in updated:
        appr = await db.get(Approval, aid)
        if appr is not None and appr.task_id not in closed_tasks:
            await _close_task_if_done(appr.task_id, db)
            closed_tasks.add(appr.task_id)
    await db.commit()
    return BatchResult(updated=updated, skipped=skipped)
