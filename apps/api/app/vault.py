"""Per-client credential vault.

Secrets are encrypted at rest and only ever decrypted server-side via
`use_credential` (which audits every access). The API and agents never
receive the plaintext — agents only learn which services are on file.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_audit
from app.crypto import decrypt, encrypt
from app.models.credential import Credential


async def store_credential(
    db: AsyncSession,
    *,
    client_id: uuid.UUID,
    service: str,
    secret: str,
    username: str | None = None,
    scope: str | None = None,
    expires_at: datetime | None = None,
) -> Credential:
    """Create or rotate the (client, service) credential. Encrypts the
    secret; never store or log plaintext."""
    existing = await db.scalar(
        select(Credential).where(
            Credential.client_id == client_id, Credential.service == service
        )
    )
    ciphertext = encrypt(secret)
    if existing is None:
        cred = Credential(
            client_id=client_id,
            service=service,
            username=username,
            secret_ciphertext=ciphertext,
            scope=scope,
            expires_at=expires_at,
        )
        db.add(cred)
    else:
        existing.secret_ciphertext = ciphertext
        existing.username = username
        existing.scope = scope
        existing.expires_at = expires_at
        cred = existing
    await db.flush()
    return cred


async def list_credentials(
    db: AsyncSession, client_id: uuid.UUID
) -> list[Credential]:
    return list(
        (
            await db.scalars(
                select(Credential)
                .where(Credential.client_id == client_id)
                .order_by(Credential.service)
            )
        ).all()
    )


def is_expired(cred: Credential) -> bool:
    return cred.expires_at is not None and cred.expires_at < datetime.now(UTC)


async def use_credential(
    db: AsyncSession,
    *,
    client_id: uuid.UUID,
    service: str,
    actor: str,
    purpose: str,
) -> str:
    """Server-side ONLY. Decrypts and returns the secret for an
    authenticated action, recording an audited access. Never expose the
    return value through the API or to agent/model context."""
    cred = await db.scalar(
        select(Credential).where(
            Credential.client_id == client_id, Credential.service == service
        )
    )
    if cred is None:
        raise LookupError(f"No credential on file for '{service}'")
    if is_expired(cred):
        raise LookupError(f"Credential for '{service}' has expired")
    cred.last_accessed_at = datetime.now(UTC)
    await record_audit(
        db,
        actor=actor,
        action="credential.accessed",
        subject=f"credential:{cred.id}",
        client_id=client_id,
        after={"service": service, "purpose": purpose},
    )
    await db.flush()
    return decrypt(cred.secret_ciphertext)


async def credential_availability(
    db: AsyncSession,
    client_id: uuid.UUID,
    *,
    actor: str,
) -> list[dict]:
    """What an agent is allowed to learn: which services have a
    credential on file and whether it is expired — never the secret.
    The lookup itself is an audited access."""
    creds = await list_credentials(db, client_id)
    await record_audit(
        db,
        actor=actor,
        action="credential.listed",
        subject=f"client:{client_id}",
        client_id=client_id,
        after={"services": [c.service for c in creds]},
    )
    await db.flush()
    return [
        {
            "service": c.service,
            "username": c.username,
            "expired": is_expired(c),
        }
        for c in creds
    ]
