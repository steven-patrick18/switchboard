import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db import get_db
from app.models import Client, Project, Task, User
from app.schemas.tasks import TaskOut

router = APIRouter(prefix="/clients/{client_id}", tags=["tasks"])


@router.get("/tasks", response_model=list[TaskOut])
async def list_tasks(
    client_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Task]:
    client = await db.get(Client, client_id)
    if client is None or client.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Client not found"
        )
    result = await db.scalars(
        select(Task)
        .join(Project, Project.id == Task.project_id)
        .where(Project.client_id == client_id)
        .order_by(Task.created_at)
    )
    return list(result)
