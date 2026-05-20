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


# Per-form pointer to where the real agency form lives. Switchboard
# does NOT generate or replicate these — they're proprietary templates
# (NECA's logo, USAC's branding, per-state PUC layouts). The operator
# downloads the authoritative PDF from the agency, fills it in with
# the data the platform prepared, and attaches it to the outbound
# email. Anything else risks the regulator rejecting a knock-off form.
_REAL_FORM_SOURCES: dict[str, str] = {
    "neca-ocn-2": (
        "Download the official NECA Company Code Request Form from "
        "https://www.neca.org/about/company-codes — fill in the values "
        "shown above, scan the signed copy, then attach to this email "
        "alongside the signed Letter of Agency."
    ),
    "neca": (
        "Download the official NECA Company Code Request Form from "
        "https://www.neca.org/about/company-codes ."
    ),
    "fcc 499-a": (
        "FCC Form 499-A is filed through USAC E-File at "
        "https://forms.universalservice.org — log in with the client's "
        "FRN, complete the form using the values shown above, then "
        "either submit through E-File or print + attach to this email."
    ),
    "fcc 499-q": (
        "FCC Form 499-Q quarterly is filed through USAC E-File at "
        "https://forms.universalservice.org ."
    ),
    "rmd": (
        "RMD entries are filed in the FCC Robocall Mitigation Database "
        "at https://fccprod.servicenowservices.com/rmd — use the values "
        "shown above and follow the FCC's online flow."
    ),
    "section_214": (
        "FCC Section 214 international authority is filed through the "
        "FCC's electronic filing system. Use the values shown above."
    ),
}


def _real_form_source(form: str) -> str:
    fkey = (form or "").lower()
    for key, msg in _REAL_FORM_SOURCES.items():
        if fkey.startswith(key):
            return msg
    return (
        f"Download the official {form} from the issuing agency's site, "
        "fill it in using the values shown above, then attach to this "
        "email."
    )


async def _execute_filing(
    approval: Approval, client_id: uuid.UUID, db: AsyncSession
) -> str:
    """Record a filing in the Document Hub and build the email packet.

    The platform does NOT generate the real agency form — that's the
    operator's job (download the authoritative template from the
    agency, fill it in using the values shown in the approval card,
    attach to the outbound email). What we keep on the Approval is
    the structured data the operator copies from, plus a Document Hub
    entry as the forensic record that this filing was approved at
    this point in time."""
    payload = approval.payload or {}
    form = str(payload.get("form") or "filing")
    version = await _next_version(db, client_id, form)
    doc = Document(client_id=client_id, type=form, version=version)
    db.add(doc)
    await db.flush()
    approval.result_document_id = doc.id

    legal_name, ein, _ = await _intake_for(db, client_id)
    from_address = await _client_from_address(db, client_id)
    packet = build_email_packet(
        form=form,
        payload=payload,
        legal_name=legal_name,
        ein=ein,
        from_address=from_address,
        summary=str(payload.get("summary") or ""),
    ).to_json()
    # Replace the generic placeholder with a real, actionable pointer
    # to the agency's authoritative form.
    packet["attachments_note"] = _real_form_source(form)
    approval.email_packet = packet

    return (
        f"Recorded {form} (v{version}) in the Document Hub. The values "
        f"the operator approved are preserved on this approval; the "
        f"actual filing is submitted via the agency's authoritative "
        f"form (downloaded from their site and filled in using these "
        f"values)."
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
