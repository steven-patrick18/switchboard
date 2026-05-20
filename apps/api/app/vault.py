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
    url: str | None = None,
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
            url=url,
        )
        db.add(cred)
    else:
        existing.secret_ciphertext = ciphertext
        existing.username = username
        existing.scope = scope
        existing.expires_at = expires_at
        existing.url = url
        cred = existing
    await db.flush()
    return cred


async def update_credential(
    db: AsyncSession,
    cred: Credential,
    *,
    url: str | None | type(...) = ...,
    username: str | None | type(...) = ...,
    scope: str | None | type(...) = ...,
    expires_at: datetime | None | type(...) = ...,
    new_secret: str | None = None,
) -> Credential:
    """Patch metadata on an existing credential — the operator can edit
    URL, username, scope, expiration without rotating the secret. To
    actually rotate the password, pass new_secret. The sentinel `...`
    means 'leave that field untouched' (so we distinguish 'clear the
    URL' from 'don't change the URL')."""
    if url is not ...:
        cred.url = url
    if username is not ...:
        cred.username = username
    if scope is not ...:
        cred.scope = scope
    if expires_at is not ...:
        cred.expires_at = expires_at
    if new_secret is not None:
        cred.secret_ciphertext = encrypt(new_secret)
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


# --- Client email / SMTP resolution --------------------------------
# The client_email credential is the per-client email login (Gmail
# app password, Outlook app password, custom-domain SMTP) the
# platform uses to send filings to NECA / FCC / state PUCs ON
# BEHALF of the client. Filings must originate from the client's
# address (not Switchboard's) for chain-of-custody — regulators
# reply to whoever sent the mail, so the client must be on the From
# line.
_CLIENT_EMAIL_SERVICES = ("client_email", "email", "smtp")

# Common provider SMTP defaults — used when the operator stores the
# credential without filling in the url field. Maps email domain →
# (host, port, use_tls).
_SMTP_PROVIDER_DEFAULTS = {
    "gmail.com": ("smtp.gmail.com", 587, True),
    "googlemail.com": ("smtp.gmail.com", 587, True),
    "outlook.com": ("smtp-mail.outlook.com", 587, True),
    "hotmail.com": ("smtp-mail.outlook.com", 587, True),
    "live.com": ("smtp-mail.outlook.com", 587, True),
    "icloud.com": ("smtp.mail.me.com", 587, True),
    "me.com": ("smtp.mail.me.com", 587, True),
    "mac.com": ("smtp.mail.me.com", 587, True),
    "yahoo.com": ("smtp.mail.yahoo.com", 587, True),
    "aol.com": ("smtp.aol.com", 587, True),
    "zoho.com": ("smtp.zoho.com", 587, True),
    "fastmail.com": ("smtp.fastmail.com", 587, True),
    "protonmail.com": ("smtp.protonmail.com", 587, True),
}


def _parse_smtp_url(raw: str | None) -> tuple[str, int, bool] | None:
    """Parse stored URL field into (host, port, use_tls). Accepts:
      - 'smtp.gmail.com'                     -> ('smtp.gmail.com', 587, True)
      - 'smtp.gmail.com:465'                 -> ('smtp.gmail.com', 465, False)
      - 'smtps://smtp.gmail.com:465'         -> ('smtp.gmail.com', 465, False)
      - 'smtp://smtp.mail.example.com:587'   -> ('smtp.mail.example.com', 587, True)
    Returns None if the input doesn't look like an SMTP host."""
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    use_tls = True
    if s.startswith("smtps://"):
        s = s[len("smtps://"):]
        use_tls = False  # implicit TLS on port 465; we wrap with SSL
    elif s.startswith("smtp://"):
        s = s[len("smtp://"):]
    # strip path / query if user pasted a full URL by mistake
    s = s.split("/", 1)[0]
    if ":" in s:
        host, _, port_s = s.partition(":")
        try:
            port = int(port_s)
        except ValueError:
            return None
        # Port 465 is implicit-SSL; everything else assume STARTTLS.
        if port == 465:
            use_tls = False
        return host, port, use_tls
    return s, 587, True


def _infer_smtp_from_email(email: str) -> tuple[str, int, bool] | None:
    if "@" not in email:
        return None
    domain = email.split("@", 1)[1].lower().strip()
    return _SMTP_PROVIDER_DEFAULTS.get(domain)


async def get_client_email_credential(
    db: AsyncSession, client_id: uuid.UUID, *, actor: str
) -> tuple[str, str, str, int, bool] | None:
    """Resolve the client's email login for sending filings.

    Returns (from_address, password, host, port, use_tls) or None.

    Looks up the first credential whose service is one of the known
    aliases ('client_email', 'email', 'smtp'). The credential's `url`
    field can carry the SMTP host explicitly; if blank we infer from
    the email domain. Decrypts via the audited `use_credential` path
    so every send leaves a forensic record."""
    cred = None
    for svc in _CLIENT_EMAIL_SERVICES:
        cred = await db.scalar(
            select(Credential).where(
                Credential.client_id == client_id, Credential.service == svc
            )
        )
        if cred is not None:
            break
    if cred is None or is_expired(cred) or not cred.username:
        return None
    smtp = _parse_smtp_url(cred.url) or _infer_smtp_from_email(cred.username)
    if smtp is None:
        return None
    secret = await use_credential(
        db,
        client_id=client_id,
        service=cred.service,
        actor=actor,
        purpose="outbound_filing_email",
    )
    host, port, use_tls = smtp
    return cred.username, secret, host, port, use_tls


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
