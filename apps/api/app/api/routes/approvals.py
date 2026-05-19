import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
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


@router.get("", response_model=list[ApprovalOut])
async def list_approvals(
    decision: str = Query(default=DECISION_PENDING),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApprovalOut]:
    stmt = _OWNED.where(Client.owner_id == user.id)
    if decision != "all":
        stmt = stmt.where(Approval.decision == decision)
    rows = (await db.execute(stmt.order_by(Approval.ts.desc()))).all()
    return [_out(a, c) for a, c in rows]


@router.get("/{approval_id}", response_model=ApprovalOut)
async def get_approval(
    approval_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApprovalOut:
    approval, client = await _load_owned(approval_id, user, db)
    return _out(approval, client)


@router.post("/{approval_id}/approve", response_model=ApprovalOut)
async def approve(
    approval_id: uuid.UUID,
    body: ApproveBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApprovalOut:
    approval, client = await _load_owned(approval_id, user, db)
    _require_pending(approval)
    if body.payload_override is not None:
        approval.payload = body.payload_override
        approval.decision = DECISION_EDITED
    else:
        approval.decision = DECISION_APPROVED
    approval.reviewer_id = user.id
    approval.note = body.note
    approval.ts = datetime.now(UTC)
    approval.execution_result = await execute_approval(approval, client.id, db)
    approval.executed_at = datetime.now(UTC)
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
    await db.commit()
    return _out(approval, client)


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
        if body.decision == DECISION_APPROVED:
            approval.execution_result = await execute_approval(
                approval, row[1].id, db
            )
            approval.executed_at = datetime.now(UTC)
        updated.append(aid)
    await db.commit()
    return BatchResult(updated=updated, skipped=skipped)
