import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


async def record_audit(
    db: AsyncSession,
    *,
    actor: str,
    action: str,
    subject: str,
    client_id: uuid.UUID | None = None,
    before: dict | None = None,
    after: dict | None = None,
) -> AuditLog:
    """Append one immutable audit entry. Flush-only — it joins the
    caller's transaction and is committed with it. There is deliberately
    no update/delete path: the trail is the legal record."""
    entry = AuditLog(
        actor=actor,
        action=action,
        subject=subject,
        client_id=client_id,
        before=before,
        after=after,
    )
    db.add(entry)
    await db.flush()
    return entry
