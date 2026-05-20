"""Public, token-scoped surface for the client.

A client opens https://.../c/<token> and gets:
- their company's intake form (read + write)
- the mandated-document list with sample/upload buttons
- a write-only credentials drop-zone (operator can see they exist; the
  client never sees previously-stored secrets — they can only add new)

Every write audits with actor=`client:{link.id}`. The token only ever
unlocks the bound client_id, so a leaked link is automatically scoped.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage
from app.audit import record_audit
from app.db import get_db
from app.doc_samples import get_sample
from app.intake import evaluate
from app.models import (
    Client,
    ClientIntake,
    ClientLink,
    Credential,
    Document,
)
from app.schemas.client_link import ClientLinkUsage
from app.schemas.credentials import CredentialUpsert
from app.schemas.intake import (
    CompletenessOut,
    DocumentOut,
    IntakeStatus,
    IntakeUpsert,
    RequiredDocOut,
)
from app.vault import store_credential

router = APIRouter(prefix="/client-portal/{token}", tags=["client-portal"])


async def _resolve_link(
    token: str, db: AsyncSession
) -> tuple[ClientLink, Client]:
    """Resolve a token to (link, client) or 404. Touch last_used_at on
    every successful resolution so the operator can see activity."""
    link = await db.scalar(select(ClientLink).where(ClientLink.token == token))
    if link is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invalid link"
        )
    if link.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Link revoked"
        )
    if link.expires_at is not None:
        # SQLite returns naive datetimes even when the column is
        # timezone-aware; treat the stored value as UTC so the comparison
        # against `datetime.now(UTC)` works on both backends.
        expires = link.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires <= datetime.now(UTC):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Link expired"
            )
    client = await db.get(Client, link.client_id)
    if client is None:
        # Cascade-delete leaves a stale link unreachable.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Client not found"
        )
    link.last_used_at = datetime.now(UTC)
    return link, client


def _client_actor(link: ClientLink) -> str:
    return f"client:{link.id}"


async def _status(
    client_id: uuid.UUID, db: AsyncSession
) -> IntakeStatus:
    intake = await db.scalar(
        select(ClientIntake).where(ClientIntake.client_id == client_id)
    )
    doc_types = set(
        (
            await db.scalars(
                select(Document.type).where(
                    Document.client_id == client_id,
                    Document.s3_key.is_not(None),
                )
            )
        ).all()
    )
    c = evaluate(intake, doc_types)
    return IntakeStatus(
        intake=intake,
        completeness=CompletenessOut(
            complete=c.complete,
            stage=c.stage,
            missing_fields=c.missing_fields,
            missing_documents=c.missing_documents,
            required_documents=[
                RequiredDocOut.model_validate(d) for d in c.required_documents
            ],
        ),
    )


@router.get("", response_model=ClientLinkUsage)
async def open_link(
    token: str, db: AsyncSession = Depends(get_db)
) -> ClientLinkUsage:
    """Lightweight handshake the public page uses to render its header
    (company name) without exposing the operator's broader workspace."""
    link, client = await _resolve_link(token, db)
    await db.commit()
    return ClientLinkUsage(
        client_name=client.name, stage=client.stage, label=link.label
    )


@router.get("/intake", response_model=IntakeStatus)
async def get_intake(token: str, db: AsyncSession = Depends(get_db)) -> IntakeStatus:
    link, client = await _resolve_link(token, db)
    st = await _status(client.id, db)
    await db.commit()
    return st


@router.put("/intake", response_model=IntakeStatus)
async def upsert_intake(
    token: str,
    body: IntakeUpsert,
    db: AsyncSession = Depends(get_db),
) -> IntakeStatus:
    link, client = await _resolve_link(token, db)
    intake = await db.scalar(
        select(ClientIntake).where(ClientIntake.client_id == client.id)
    )
    if intake is None:
        intake = ClientIntake(client_id=client.id)
        db.add(intake)
    changed: list[str] = []
    for field, value in body.model_dump(exclude_unset=True).items():
        if getattr(intake, field, None) != value:
            changed.append(field)
        setattr(intake, field, value)
    if changed:
        await record_audit(
            db,
            actor=_client_actor(link),
            action="intake.upserted_by_client",
            subject=f"client:{client.id}",
            client_id=client.id,
            after={"fields_changed": changed},
        )
    st = await _status(client.id, db)
    await db.commit()
    return st


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(
    token: str, db: AsyncSession = Depends(get_db)
) -> list[Document]:
    _, client = await _resolve_link(token, db)
    rows = list(
        (
            await db.scalars(
                select(Document)
                .where(Document.client_id == client.id)
                .order_by(Document.created_at)
            )
        ).all()
    )
    await db.commit()
    return rows


@router.post(
    "/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    token: str,
    type: str = Form(min_length=1, max_length=64),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> Document:
    link, client = await _resolve_link(token, db)
    data = await file.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )
    try:
        digest, size = storage.store_bytes(data)
    except storage.TooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
    existing_max = await db.scalar(
        select(Document.version)
        .where(Document.client_id == client.id, Document.type == type)
        .order_by(Document.version.desc())
        .limit(1)
    )
    doc = Document(
        client_id=client.id,
        type=type,
        version=(existing_max or 0) + 1,
        s3_key=digest,
        filename=file.filename,
        mime=file.content_type,
        size_bytes=size,
    )
    db.add(doc)
    await db.flush()
    await record_audit(
        db,
        actor=_client_actor(link),
        action="document.uploaded_by_client",
        subject=f"document:{doc.id}",
        client_id=client.id,
        after={"type": type, "version": doc.version, "size_bytes": size},
    )
    await db.commit()
    await db.refresh(doc)
    return doc


@router.get("/documents/{doc_id}/download")
async def download_document(
    token: str,
    doc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Client can re-fetch a document they uploaded — useful if they
    want to confirm what they sent."""
    _, client = await _resolve_link(token, db)
    doc = await db.get(Document, doc_id)
    if doc is None or doc.client_id != client.id or not doc.s3_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )
    try:
        data = storage.read_bytes(doc.s3_key)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document bytes missing from storage.",
        ) from None
    filename = doc.filename or f"{doc.type}-v{doc.version}"
    await db.commit()
    return Response(
        content=data,
        media_type=doc.mime or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/documents/{doc_key}/sample")
async def download_document_sample(
    token: str,
    doc_key: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Per-document spec the client can read to know what to send."""
    await _resolve_link(token, db)
    sample = get_sample(doc_key)
    if sample is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No sample for document '{doc_key}'.",
        )
    filename, text = sample
    await db.commit()
    return Response(
        content=text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/credentials", status_code=status.HTTP_201_CREATED
)
async def add_credential(
    token: str,
    body: CredentialUpsert,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Write-only from the client side: they can add a new credential
    for a service, but the portal never exposes the existing list or
    secret values. Operator's view is the single source of truth."""
    link, client = await _resolve_link(token, db)
    await store_credential(
        db,
        client_id=client.id,
        service=body.service,
        secret=body.secret,
        username=body.username,
        scope=body.scope,
        expires_at=body.expires_at,
    )
    await record_audit(
        db,
        actor=_client_actor(link),
        action="credential.added_by_client",
        subject=f"credential:{body.service}",
        client_id=client.id,
        after={"service": body.service},
    )
    await db.commit()
    return {"service": body.service, "status": "stored"}


@router.get("/credentials/services", response_model=list[str])
async def list_credential_services(
    token: str, db: AsyncSession = Depends(get_db)
) -> list[str]:
    """Only the *names* of services already on file (so the client can
    avoid re-entering). Never the secret value, never the username."""
    _, client = await _resolve_link(token, db)
    services = list(
        (
            await db.scalars(
                select(Credential.service).where(Credential.client_id == client.id)
            )
        ).all()
    )
    await db.commit()
    return services
