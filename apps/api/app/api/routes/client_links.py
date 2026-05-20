"""Operator-side management of share links for a client.

The client-facing surface lives in client_portal.py; this module only
mints, lists, and revokes the links. All actions are owner-scoped.
"""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.audit import record_audit
from app.db import get_db
from app.models import Client, ClientLink, User
from app.schemas.client_link import ClientLinkCreate, ClientLinkOut

router = APIRouter(prefix="/clients/{client_id}/links", tags=["client-links"])


async def _owned_client(
    client_id: uuid.UUID, user: User, db: AsyncSession
) -> Client:
    client = await db.get(Client, client_id)
    if client is None or client.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Client not found"
        )
    return client


@router.post("", response_model=ClientLinkOut, status_code=status.HTTP_201_CREATED)
async def create_link(
    client_id: uuid.UUID,
    body: ClientLinkCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ClientLink:
    await _owned_client(client_id, user, db)
    link = ClientLink(
        client_id=client_id,
        label=body.label,
        expires_at=(
            datetime.now(UTC) + timedelta(hours=body.expires_in_hours)
            if body.expires_in_hours
            else None
        ),
    )
    db.add(link)
    await db.flush()
    await record_audit(
        db,
        actor=user.email,
        action="client_link.created",
        subject=f"client_link:{link.id}",
        client_id=client_id,
        after={"label": link.label, "expires_at": link.expires_at},
    )
    await db.commit()
    await db.refresh(link)
    return link


@router.get("", response_model=list[ClientLinkOut])
async def list_links(
    client_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ClientLink]:
    await _owned_client(client_id, user, db)
    result = await db.scalars(
        select(ClientLink)
        .where(ClientLink.client_id == client_id)
        .order_by(ClientLink.created_at.desc())
    )
    return list(result)


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_link(
    client_id: uuid.UUID,
    link_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _owned_client(client_id, user, db)
    link = await db.get(ClientLink, link_id)
    if link is None or link.client_id != client_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Link not found"
        )
    if link.revoked_at is None:
        link.revoked_at = datetime.now(UTC)
        await record_audit(
            db,
            actor=user.email,
            action="client_link.revoked",
            subject=f"client_link:{link.id}",
            client_id=client_id,
        )
    await db.commit()
