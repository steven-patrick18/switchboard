import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db import get_db
from app.doc_samples import build_request_pack_pdf, get_sample
from app.intake import evaluate, resolve_required_documents
from app.models import Client, ClientIntake, Document, User
from app.schemas.intake import (
    CompletenessOut,
    DocumentOut,
    DocumentRegister,
    IntakeStatus,
    IntakeUpsert,
    RequiredDocOut,
)

router = APIRouter(prefix="/clients/{client_id}", tags=["intake"])


async def _owned_client(client_id: uuid.UUID, user: User, db: AsyncSession) -> Client:
    client = await db.get(Client, client_id)
    if client is None or client.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Client not found"
        )
    return client


async def _status(client_id: uuid.UUID, db: AsyncSession) -> IntakeStatus:
    intake = await db.scalar(
        select(ClientIntake).where(ClientIntake.client_id == client_id)
    )
    doc_types = set(
        (
            await db.scalars(
                select(Document.type).where(Document.client_id == client_id)
            )
        ).all()
    )
    c = evaluate(intake, doc_types)
    return IntakeStatus(
        intake=intake,
        completeness=CompletenessOut(
            complete=c.complete,
            missing_fields=c.missing_fields,
            missing_documents=c.missing_documents,
            required_documents=[
                RequiredDocOut.model_validate(d) for d in c.required_documents
            ],
        ),
    )


@router.get("/intake", response_model=IntakeStatus)
async def get_intake(
    client_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> IntakeStatus:
    await _owned_client(client_id, user, db)
    return await _status(client_id, db)


@router.put("/intake", response_model=IntakeStatus)
async def upsert_intake(
    client_id: uuid.UUID,
    body: IntakeUpsert,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> IntakeStatus:
    await _owned_client(client_id, user, db)
    intake = await db.scalar(
        select(ClientIntake).where(ClientIntake.client_id == client_id)
    )
    if intake is None:
        intake = ClientIntake(client_id=client_id)
        db.add(intake)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(intake, field, value)
    await db.commit()
    return await _status(client_id, db)


@router.post("/intake/submit", response_model=IntakeStatus)
async def submit_intake(
    client_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> IntakeStatus:
    await _owned_client(client_id, user, db)
    st = await _status(client_id, db)
    if not st.completeness.complete:
        # The mandate: the client cannot be marked onboarded until every
        # required datum and document is captured — so they are never
        # re-disturbed mid-process.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Intake incomplete — cannot submit.",
                "missing_fields": st.completeness.missing_fields,
                "missing_documents": st.completeness.missing_documents,
            },
        )
    intake = await db.scalar(
        select(ClientIntake).where(ClientIntake.client_id == client_id)
    )
    intake.submitted_at = datetime.now(UTC)
    await db.commit()
    return await _status(client_id, db)


@router.post(
    "/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED
)
async def register_document(
    client_id: uuid.UUID,
    body: DocumentRegister,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Document:
    await _owned_client(client_id, user, db)
    doc = Document(client_id=client_id, type=body.type, s3_key=body.s3_key)
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(
    client_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Document]:
    await _owned_client(client_id, user, db)
    result = await db.scalars(
        select(Document)
        .where(Document.client_id == client_id)
        .order_by(Document.created_at)
    )
    return list(result)


@router.get("/documents/request-pack")
async def download_request_pack(
    client_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """One emailable packet of every mandated document's spec, tailored
    to this client's intake (international / target states included)."""
    client = await _owned_client(client_id, user, db)
    intake = await db.scalar(
        select(ClientIntake).where(ClientIntake.client_id == client_id)
    )
    docs = resolve_required_documents(intake)
    filename, pdf_bytes = build_request_pack_pdf(client.name, docs)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/documents/{doc_key}/sample")
async def download_document_sample(
    client_id: uuid.UUID,
    doc_key: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Downloadable spec/template for a mandated document — the operator
    forwards this to the client so they send the correct document."""
    await _owned_client(client_id, user, db)
    sample = get_sample(doc_key)
    if sample is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No sample for document '{doc_key}'.",
        )
    filename, text = sample
    return Response(
        content=text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
