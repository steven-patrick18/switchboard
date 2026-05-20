"""Execute an approved action's in-platform follow-through.

Honest scope: this advances workspace state and materializes Document
Hub records so the launch progresses and stays auditable. It performs
NO external network I/O — real FCC submission and Documenso e-signature
are the integration layer (later phases) and are explicitly called out
in the recorded result.
"""

import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.email_packets import build_email_packet
from app.models.approval import Approval
from app.models.client_intake import ClientIntake
from app.models.credential import Credential
from app.models.document import Document
from app.pdf_builders import (
    build_generic_filing_pdf,
    build_letter_of_agency_pdf,
    build_neca_ocn_2_pdf,
)
from app.storage import store_bytes


async def _next_version(db: AsyncSession, client_id: uuid.UUID, doc_type: str) -> int:
    current = await db.scalar(
        select(func.max(Document.version)).where(
            Document.client_id == client_id, Document.type == doc_type
        )
    )
    return (current or 0) + 1


async def _intake_for(
    db: AsyncSession, client_id: uuid.UUID
) -> tuple[str | None, str | None, str | None]:
    """(legal_name, ein, officer_email) for the email packet header.
    The officer_email is used as the From line if a stored
    client_email credential isn't on file yet."""
    intake = await db.scalar(
        select(ClientIntake).where(ClientIntake.client_id == client_id)
    )
    if intake is None:
        return None, None, None
    return intake.legal_name, intake.ein, intake.officer_email


async def _client_from_address(db: AsyncSession, client_id: uuid.UUID) -> str:
    """The From line for an outbound filing — must be the CLIENT, not
    Switchboard. Resolution order:
      1. The username on the stored client_email/email/smtp credential
         (this is what the SMTP login will actually use)
      2. The officer_email on the client's intake
      3. A clear placeholder so the operator knows to add a credential
    """
    from app.models.credential import Credential  # noqa: PLC0415

    for svc in ("client_email", "email", "smtp"):
        cred = await db.scalar(
            select(Credential).where(
                Credential.client_id == client_id, Credential.service == svc
            )
        )
        if cred is not None and cred.username:
            return cred.username
    intake = await db.scalar(
        select(ClientIntake).where(ClientIntake.client_id == client_id)
    )
    if intake is not None and intake.officer_email:
        return intake.officer_email
    return "[client email — add a 'client_email' credential in the vault]"


async def _persist_pdf(
    db: AsyncSession,
    *,
    client_id: uuid.UUID,
    doc_type: str,
    filename: str,
    data: bytes,
) -> Document:
    """Content-address the PDF bytes, register a Document row pointing
    at the on-disk artifact, and return it. Versioning matches the
    rest of the Document Hub — bumping each time we regenerate."""
    sha, size = store_bytes(data)
    version = await _next_version(db, client_id, doc_type)
    doc = Document(
        client_id=client_id,
        type=doc_type,
        version=version,
        s3_key=sha,
        filename=filename,
        mime="application/pdf",
        size_bytes=size,
    )
    db.add(doc)
    await db.flush()
    return doc


def _pdf_filename(form: str, legal_name: str | None, version: int) -> str:
    """Sanitize for a filesystem-safe attachment name without losing
    enough context that the operator can identify it."""
    base = (legal_name or "client").strip()
    safe_name = "".join(
        c if c.isalnum() or c in "-_." else "_" for c in base
    ).strip("_") or "client"
    safe_form = "".join(
        c if c.isalnum() or c in "-_." else "_" for c in form
    ).strip("_") or "filing"
    return f"{safe_form}__{safe_name}__v{version}.pdf"


async def _execute_filing(
    approval: Approval, client_id: uuid.UUID, db: AsyncSession
) -> str:
    payload = approval.payload or {}
    form = str(payload.get("form") or "filing")
    legal_name, ein, _ = await _intake_for(db, client_id)

    # Generate the actual PDF(s) the operator will mail to the agency.
    # Picks the right builder per form; everything else falls back to
    # a generic key/value dump so we never end up with a Document Hub
    # stub with no file behind it.
    attachments: list[dict] = []
    fkey = form.lower()
    if fkey.startswith("neca-ocn"):
        # NECA-OCN-2 is a two-document package: the form + the LOA.
        ocn_pdf = build_neca_ocn_2_pdf(payload, legal_name)
        ocn_doc = await _persist_pdf(
            db,
            client_id=client_id,
            doc_type=form,
            filename=_pdf_filename(form, legal_name, 1),
            data=ocn_pdf,
        )
        # The LOA filename derives from the form name so they group
        # cleanly in the Document Hub listing.
        loa_pdf = build_letter_of_agency_pdf(payload, legal_name)
        loa_doc = await _persist_pdf(
            db,
            client_id=client_id,
            doc_type=f"{form}__LOA",
            filename=_pdf_filename(f"{form}-LOA", legal_name, 1),
            data=loa_pdf,
        )
        approval.result_document_id = ocn_doc.id
        attachments = [
            {
                "document_id": str(ocn_doc.id),
                "filename": ocn_doc.filename,
                "mime": ocn_doc.mime,
                "size_bytes": ocn_doc.size_bytes,
            },
            {
                "document_id": str(loa_doc.id),
                "filename": loa_doc.filename,
                "mime": loa_doc.mime,
                "size_bytes": loa_doc.size_bytes,
            },
        ]
    else:
        # FCC 499 / RMD / Section 214 / etc. — generic dump for now;
        # add dedicated builders as each form gets templated.
        generic_pdf = build_generic_filing_pdf(form, payload, legal_name)
        gen_doc = await _persist_pdf(
            db,
            client_id=client_id,
            doc_type=form,
            filename=_pdf_filename(form, legal_name, 1),
            data=generic_pdf,
        )
        approval.result_document_id = gen_doc.id
        attachments = [
            {
                "document_id": str(gen_doc.id),
                "filename": gen_doc.filename,
                "mime": gen_doc.mime,
                "size_bytes": gen_doc.size_bytes,
            },
        ]

    # Build the prefilled email packet so the operator can one-click
    # send (or copy-paste) the filing. IMPORTANT: From line is the
    # CLIENT, not Switchboard. The packet now carries attachment IDs
    # so /send-email can read the bytes and attach them.
    from_address = await _client_from_address(db, client_id)
    packet = build_email_packet(
        form=form,
        payload=payload,
        legal_name=legal_name,
        ein=ein,
        from_address=from_address,
        summary=str(payload.get("summary") or ""),
    ).to_json()
    packet["attachments"] = attachments
    approval.email_packet = packet

    primary_doc = await db.get(Document, approval.result_document_id)
    primary_version = primary_doc.version if primary_doc else 1
    file_summary = ", ".join(a["filename"] for a in attachments)
    return (
        f"Recorded {form} (v{primary_version}) in the Document Hub with "
        f"{len(attachments)} PDF attachment(s): {file_summary}. Email "
        f"packet ready to send from the client's address. The external "
        f"submission to the agency is performed by the operator (or, "
        f"later, the integration layer)."
    )


async def _execute_signature(
    approval: Approval, client_id: uuid.UUID, db: AsyncSession
) -> str:
    payload = approval.payload or {}
    doc_type = str(payload.get("doc_type") or "document")
    recipient = str(payload.get("recipient") or "the recipient")
    version = await _next_version(db, client_id, doc_type)
    doc = Document(client_id=client_id, type=doc_type, version=version)
    db.add(doc)
    await db.flush()
    approval.result_document_id = doc.id
    # Even before Documenso integration is wired, give the operator a
    # ready-to-send email so they can dispatch the doc out-of-band.
    legal_name, ein, _ = await _intake_for(db, client_id)
    from_address = await _client_from_address(db, client_id)
    body = (
        f"Please find attached: {doc_type} for {legal_name or '[client]'} "
        f"({ein or 'EIN TBD'}).\n\nReply with your signature or use the "
        f"e-sign link once wired.\n\nThank you,\n{from_address}"
    )
    approval.email_packet = {
        "to": recipient,
        "from_address": from_address,
        "subject": f"Please sign: {doc_type} — {legal_name or 'client'}",
        "body": body,
        "cc": None,
        "attachments_note": f"Attach: {doc_type} v{version} from Document Hub.",
    }
    return (
        f"Recorded a signature request for {doc_type} (v{version}) to "
        f"{recipient} in the Document Hub. The Documenso send is performed "
        f"by the integration layer (Week 4-5) — not yet wired. A ready-"
        f"to-send email packet is attached to this approval."
    )


async def _execute_portal_action(
    approval: Approval, client_id: uuid.UUID, db: AsyncSession
) -> str:
    from app.portal_actions import get_action, missing_params

    payload = approval.payload or {}
    service = str(payload.get("service") or "")
    action = str(payload.get("action") or "")
    spec = get_action(service, action)

    # Validate the registry-known shape before touching the vault.
    if spec is not None:
        missing = missing_params(spec, payload.get("params"))
        if missing:
            return (
                f"Approved, but cannot execute {spec.label}: missing "
                f"required params: {', '.join(missing)}. Add them and retry."
            )

    cred = await db.scalar(
        select(Credential).where(
            Credential.client_id == client_id, Credential.service == service
        )
    )
    if cred is None:
        return (
            f"Approved, but cannot execute: no credential on file for "
            f"'{service}'. Add it under Credentials vault and retry."
        )

    # If an integration adapter is wired for this action, decrypt the
    # credential (audited as `credential.accessed`) and invoke it. The
    # secret stays in the closure; the result is structured + safe.
    from app.integrations import get_handler
    from app.vault import use_credential

    handler = get_handler(service, action)
    integration_text: str | None = None
    if handler is not None:
        secret = await use_credential(
            db,
            client_id=client_id,
            service=service,
            actor="system",
            purpose=f"portal_action:{action}",
        )
        out = await handler(secret, payload.get("params") or {})
        integration_text = (
            f"Backend: {out.backend}. Status: {out.status}. "
            f"Detail: {out.detail}."
        )

    doc_type = f"portal_action:{service}:{action}"
    version = await _next_version(db, client_id, doc_type)
    doc = Document(client_id=client_id, type=doc_type, version=version)
    db.add(doc)
    await db.flush()
    approval.result_document_id = doc.id
    label = spec.label if spec is not None else f"'{action}' on '{service}'"
    note = "" if spec is not None else " (action not in registry — free-form)"
    base = (
        f"Authenticated {label}{note} using the stored credential "
        f"(vault id {cred.id}). Recorded in the Document Hub as "
        f"{doc_type} (v{version})."
    )
    if integration_text is not None:
        return base + " " + integration_text
    return (
        base
        + " The external portal call itself is performed by the "
        "integration layer (Playwright/HTTP, not yet wired for this action)."
    )


EXECUTORS: dict[str, Callable[[Approval, uuid.UUID, AsyncSession], Awaitable[str]]] = {
    "queue_filing_submission": _execute_filing,
    "send_document_for_signature": _execute_signature,
    "request_portal_action": _execute_portal_action,
}


async def execute_approval(
    approval: Approval, client_id: uuid.UUID, db: AsyncSession
) -> str:
    handler = EXECUTORS.get(approval.action_type)
    if handler is None:
        return (
            f"No executor registered for '{approval.action_type}'. Recorded "
            f"the decision; no follow-through performed."
        )
    return await handler(approval, client_id, db)
