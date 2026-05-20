import csv
import io
import json
import uuid
import zipfile
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage
from app.api.deps import get_current_user
from app.db import get_db
from app.doc_samples import build_request_pack_pdf, get_sample
from app.intake import evaluate, resolve_required_documents
from app.models import AuditLog, Client, ClientIntake, Document, User
from app.schemas.intake import (
    CompletenessOut,
    DocumentOut,
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
    # Only documents that actually have bytes on disk count as "provided"
    # toward intake completeness — registering a type without a file is a
    # metadata stub, not satisfaction of a mandate.
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
async def upload_document(
    client_id: uuid.UUID,
    type: str = Form(min_length=1, max_length=64),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Document:
    """Upload the actual bytes for a document of `type`. Storage is
    content-addressed (SHA-256), so re-uploading the same file is a
    no-op on disk; a new Document row is still created so version
    history shows the upload event."""
    await _owned_client(client_id, user, db)
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
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(exc),
        ) from exc
    # New version = (max existing version for this type) + 1.
    existing_max = await db.scalar(
        select(Document.version)
        .where(Document.client_id == client_id, Document.type == type)
        .order_by(Document.version.desc())
        .limit(1)
    )
    doc = Document(
        client_id=client_id,
        type=type,
        version=(existing_max or 0) + 1,
        s3_key=digest,
        filename=file.filename,
        mime=file.content_type,
        size_bytes=size,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


@router.get("/documents.zip")
async def export_client_zip(
    client_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Archive bundle of everything captured for one client: every
    uploaded document (latest version of each type) plus intake.json
    and audit.csv. Useful for handoff or compliance archiving."""
    client = await _owned_client(client_id, user, db)
    intake = await db.scalar(
        select(ClientIntake).where(ClientIntake.client_id == client_id)
    )
    docs = list(
        (
            await db.scalars(
                select(Document)
                .where(
                    Document.client_id == client_id,
                    Document.s3_key.is_not(None),
                )
                .order_by(Document.type, Document.version.desc())
            )
        ).all()
    )
    audit = list(
        (
            await db.scalars(
                select(AuditLog)
                .where(AuditLog.client_id == client_id)
                .order_by(AuditLog.ts.asc())
            )
        ).all()
    )

    buf = io.BytesIO()
    seen_types: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Intake snapshot — always present so the archive's structure
        # is predictable, even when the operator hasn't started intake.
        intake_obj = (
            {
                col.name: getattr(intake, col.name)
                for col in intake.__table__.columns
            }
            if intake is not None
            else None
        )
        zf.writestr(
            "intake.json",
            json.dumps(intake_obj, indent=2, default=str),
        )
        # Audit CSV (full trail).
        audit_buf = io.StringIO()
        w = csv.writer(audit_buf, lineterminator="\n")
        w.writerow(
            ("ts", "actor", "action", "subject", "before", "after")
        )
        for r in audit:
            w.writerow(
                (
                    r.ts.isoformat() if r.ts else "",
                    r.actor,
                    r.action,
                    r.subject,
                    json.dumps(r.before, separators=(",", ":")) if r.before else "",
                    json.dumps(r.after, separators=(",", ":")) if r.after else "",
                )
            )
        zf.writestr("audit.csv", audit_buf.getvalue())
        # Latest version of each document type only — older versions are
        # in the version history; the archive carries the canonical set.
        for d in docs:
            if d.type in seen_types:
                continue
            seen_types.add(d.type)
            try:
                data = storage.read_bytes(d.s3_key)  # type: ignore[arg-type]
            except FileNotFoundError:
                continue
            name = d.filename or f"{d.type}-v{d.version}"
            zf.writestr(f"documents/{d.type}/{name}", data)

    safe = "".join(
        ch if ch.isalnum() or ch in "-_" else "-" for ch in client.name
    ).strip("-") or "client"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{safe}-archive.zip"'
        },
    )


@router.get("/documents/{doc_id}/download")
async def download_document(
    client_id: uuid.UUID,
    doc_id: uuid.UUID,
    dl: int = 0,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Stream back the uploaded bytes. Owner-scoped via _owned_client.

    Pass `?dl=1` to force a download (Content-Disposition: attachment).
    Default (no query) is inline so the browser can preview PDFs/images
    in a new tab — useful for verifying a generated NECA-OCN-2 packet
    before emailing it out."""
    await _owned_client(client_id, user, db)
    doc = await db.get(Document, doc_id)
    if doc is None or doc.client_id != client_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found."
        )
    if not doc.s3_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document has no uploaded file.",
        )
    try:
        data = storage.read_bytes(doc.s3_key)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document bytes missing from storage.",
        ) from None
    filename = doc.filename or f"{doc.type}-v{doc.version}"
    disposition = "attachment" if dl else "inline"
    return Response(
        content=data,
        media_type=doc.mime or "application/octet-stream",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


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
