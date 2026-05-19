import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db import get_db
from app.models import AuditLog, Client, User
from app.schemas.audit import AuditOut

router = APIRouter(prefix="/clients/{client_id}", tags=["audit"])


@router.get("/audit", response_model=list[AuditOut])
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
