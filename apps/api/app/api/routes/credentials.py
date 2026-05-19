import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.audit import record_audit
from app.db import get_db
from app.models import Client, Credential, User
from app.schemas.credentials import CredentialOut, CredentialUpsert
from app.vault import list_credentials, store_credential

router = APIRouter(prefix="/clients/{client_id}", tags=["credentials"])


async def _owned_client(
    client_id: uuid.UUID, user: User, db: AsyncSession
) -> Client:
    client = await db.get(Client, client_id)
    if client is None or client.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Client not found"
        )
    return client


@router.post(
    "/credentials", response_model=CredentialOut, status_code=status.HTTP_201_CREATED
)
async def upsert_credential(
    client_id: uuid.UUID,
    body: CredentialUpsert,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Credential:
    await _owned_client(client_id, user, db)
    cred = await store_credential(
        db,
        client_id=client_id,
        service=body.service,
        secret=body.secret,
        username=body.username,
        scope=body.scope,
        expires_at=body.expires_at,
    )
    await record_audit(
        db,
        actor=user.email,
        action="credential.stored",
        subject=f"credential:{cred.id}",
        client_id=client_id,
        after={"service": body.service},  # never the secret
    )
    await db.commit()
    return cred


@router.get("/credentials", response_model=list[CredentialOut])
async def list_client_credentials(
    client_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Credential]:
    await _owned_client(client_id, user, db)
    return await list_credentials(db, client_id)


@router.delete(
    "/credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_credential(
    client_id: uuid.UUID,
    credential_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _owned_client(client_id, user, db)
    cred = await db.get(Credential, credential_id)
    if cred is None or cred.client_id != client_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found"
        )
    await db.delete(cred)
    await record_audit(
        db,
        actor=user.email,
        action="credential.deleted",
        subject=f"credential:{credential_id}",
        client_id=client_id,
        after={"service": cred.service},
    )
    await db.commit()
