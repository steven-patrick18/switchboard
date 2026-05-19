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

from app.models.approval import Approval
from app.models.document import Document


async def _next_version(db: AsyncSession, client_id: uuid.UUID, doc_type: str) -> int:
    current = await db.scalar(
        select(func.max(Document.version)).where(
            Document.client_id == client_id, Document.type == doc_type
        )
    )
    return (current or 0) + 1


async def _execute_filing(
    approval: Approval, client_id: uuid.UUID, db: AsyncSession
) -> str:
    payload = approval.payload or {}
    form = str(payload.get("form") or "filing")
    version = await _next_version(db, client_id, form)
    db.add(Document(client_id=client_id, type=form, version=version))
    return (
        f"Recorded {form} (v{version}) in the Document Hub. The external "
        f"submission to FCC is performed by the integration layer (not yet "
        f"wired) — this is the tracked internal artifact."
    )


async def _execute_signature(
    approval: Approval, client_id: uuid.UUID, db: AsyncSession
) -> str:
    payload = approval.payload or {}
    doc_type = str(payload.get("doc_type") or "document")
    recipient = str(payload.get("recipient") or "the recipient")
    version = await _next_version(db, client_id, doc_type)
    db.add(Document(client_id=client_id, type=doc_type, version=version))
    return (
        f"Recorded a signature request for {doc_type} (v{version}) to "
        f"{recipient} in the Document Hub. The Documenso send is performed "
        f"by the integration layer (Week 4-5) — not yet wired."
    )


EXECUTORS: dict[str, Callable[[Approval, uuid.UUID, AsyncSession], Awaitable[str]]] = {
    "queue_filing_submission": _execute_filing,
    "send_document_for_signature": _execute_signature,
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
