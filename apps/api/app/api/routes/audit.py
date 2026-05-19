import csv
import io
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db import get_db
from app.models import AuditLog, Client, User
from app.schemas.audit import AuditOut

router = APIRouter(tags=["audit"])

_CSV_COLUMNS = ("ts", "actor", "action", "subject", "client_id", "before", "after")


def _rows_to_csv(rows: list[AuditLog]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(_CSV_COLUMNS)
    for r in rows:
        w.writerow(
            [
                r.ts.isoformat() if r.ts else "",
                r.actor,
                r.action,
                r.subject,
                str(r.client_id) if r.client_id else "",
                json.dumps(r.before, separators=(",", ":")) if r.before else "",
                json.dumps(r.after, separators=(",", ":")) if r.after else "",
            ]
        )
    return buf.getvalue().encode("utf-8")


@router.get("/clients/{client_id}/audit", response_model=list[AuditOut])
async def list_audit(
    client_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AuditLog]:
    client = await db.get(Client, client_id)
    if client is None or client.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Client not found"
        )
    result = await db.scalars(
        select(AuditLog)
        .where(AuditLog.client_id == client_id)
        .order_by(AuditLog.ts.desc())
        .limit(limit)
    )
    return list(result)


@router.get("/clients/{client_id}/audit.csv")
async def export_client_audit_csv(
    client_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Full audit trail for one client as CSV. Bounded by the audit_logs
    natural cap (one row per consequential action) — no pagination."""
    client = await db.get(Client, client_id)
    if client is None or client.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Client not found"
        )
    rows = list(
        (
            await db.scalars(
                select(AuditLog)
                .where(AuditLog.client_id == client_id)
                .order_by(AuditLog.ts.asc())
            )
        ).all()
    )
    safe = "".join(
        ch if ch.isalnum() or ch in "-_" else "-" for ch in client.name
    ).strip("-") or "client"
    return Response(
        content=_rows_to_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{safe}-audit.csv"'
        },
    )


@router.get("/audit.csv")
async def export_operator_audit_csv(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Operator-wide audit trail: every audit row for every client the
    operator owns. The owner filter goes through the client join so the
    trail can never leak across operator boundaries."""
    rows = list(
        (
            await db.execute(
                select(AuditLog)
                .join(Client, Client.id == AuditLog.client_id)
                .where(Client.owner_id == user.id)
                .order_by(AuditLog.ts.asc())
            )
        )
        .scalars()
        .all()
    )
    return Response(
        content=_rows_to_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="audit.csv"'},
    )
