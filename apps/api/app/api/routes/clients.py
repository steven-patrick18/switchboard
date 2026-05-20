import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.audit import record_audit
from app.db import get_db
from app.models import Client, User
from app.models.client import AUTONOMY_LEVELS
from app.schemas.client import ClientCreate, ClientOut, ClientUpdate

router = APIRouter(prefix="/clients", tags=["clients"])


async def _owned_client(
    client_id: uuid.UUID, user: User, db: AsyncSession
) -> Client:
    client = await db.get(Client, client_id)
    if client is None or client.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Client not found"
        )
    return client


@router.post("", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
async def create_client(
    body: ClientCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Client:
    client = Client(owner_id=user.id, **body.model_dump())
    db.add(client)
    await db.commit()
    await db.refresh(client)
    return client


@router.get("", response_model=list[ClientOut])
async def list_clients(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Client]:
    result = await db.scalars(
        select(Client).where(Client.owner_id == user.id).order_by(Client.created_at)
    )
    return list(result)


@router.get("/{client_id}", response_model=ClientOut)
async def get_client(
    client_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Client:
    return await _owned_client(client_id, user, db)


@router.patch("/{client_id}", response_model=ClientOut)
async def update_client(
    client_id: uuid.UUID,
    body: ClientUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Client:
    client = await _owned_client(client_id, user, db)
    changes = body.model_dump(exclude_unset=True)
    new_autonomy = changes.get("autonomy_level")
    if new_autonomy is not None and new_autonomy not in AUTONOMY_LEVELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"autonomy_level must be one of {sorted(AUTONOMY_LEVELS)}",
        )
    previous_autonomy = client.autonomy_level
    for field, value in changes.items():
        setattr(client, field, value)
    if new_autonomy is not None and new_autonomy != previous_autonomy:
        # Mode changes are operationally significant — they widen or
        # narrow what agents can do without your sign-off, so audit
        # them as their own action for forensic review.
        await record_audit(
            db,
            actor=user.email,
            action="client.autonomy_changed",
            subject=f"client:{client.id}",
            client_id=client.id,
            before={"autonomy_level": previous_autonomy},
            after={"autonomy_level": new_autonomy},
        )
    await db.commit()
    await db.refresh(client)
    return client


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_client(
    client_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    client = await _owned_client(client_id, user, db)
    await db.delete(client)
    await db.commit()
