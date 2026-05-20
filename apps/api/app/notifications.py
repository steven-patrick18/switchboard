"""Best-effort SMTP notifications.

The platform never *blocks* on email — if SMTP isn't configured, or
delivery fails, the operator's UI flow proceeds unchanged. Email is a
convenience: the in-app pending-approval badge is the source of truth.

Config is resolved at call time from the DB (GUI-edited) with env
fallback so an operator's "save in Settings + send" works without an
API restart.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from sqlalchemy.ext.asyncio import AsyncSession

from app import platform_config

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    sender: str
    use_tls: bool

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.sender)


async def _load_config(db: AsyncSession) -> SmtpConfig:
    return SmtpConfig(
        host=await platform_config.smtp_host(db),
        port=await platform_config.smtp_port(db),
        user=await platform_config.smtp_user(db),
        password=await platform_config.smtp_password(db),
        sender=await platform_config.smtp_from(db),
        use_tls=await platform_config.smtp_use_tls(db),
    )


def _send_sync(
    cfg: SmtpConfig,
    to: str,
    subject: str,
    body: str,
    cc: list[str] | None = None,
) -> str:
    """Synchronous SMTP send. Returns the Message-ID so the caller can
    record it on the Approval (for later inbound-reply matching when
    IMAP/webhook integration is wired)."""
    msg = EmailMessage()
    msg["From"] = cfg.sender
    msg["To"] = to
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(cfg.host, cfg.port or 587) as s:
        if cfg.use_tls:
            s.starttls()
        if cfg.user and cfg.password:
            s.login(cfg.user, cfg.password)
        s.send_message(msg)
    return msg.get("Message-ID") or ""


async def send_email_now(
    db: AsyncSession,
    *,
    to: str,
    subject: str,
    body: str,
    cc: list[str] | None = None,
) -> tuple[bool, str | None, str | None]:
    """Synchronous send via the PLATFORM SMTP (operator-level). Used for
    internal Switchboard mail — approval-queue alerts to the operator,
    self-tests, etc. Returns (ok, message_id, error_text).

    For outbound filings to NECA/FCC/etc the From line must be the
    CLIENT, not Switchboard — use send_via_client_smtp instead."""
    cfg = await _load_config(db)
    if not cfg.enabled:
        return False, None, "SMTP not configured (set host + from in Settings)."
    try:
        mid = await asyncio.to_thread(_send_sync, cfg, to, subject, body, cc)
        return True, mid or None, None
    except Exception as exc:  # noqa: BLE001 — boundary: external SMTP
        return False, None, f"{exc.__class__.__name__}: {exc}"


def _send_via_client_sync(
    *,
    from_address: str,
    password: str,
    host: str,
    port: int,
    use_tls: bool,
    to: str,
    subject: str,
    body: str,
    cc: list[str] | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> str:
    """Open the CLIENT's SMTP server and send. Port 465 uses implicit
    SSL; everything else uses STARTTLS. Returns the Message-ID so the
    Approval row can record it for later inbound-reply matching.

    `attachments` is a list of (filename, bytes, mime) — the filings the
    operator is delivering to the agency (NECA-OCN-2.pdf, signed LOA,
    etc.). The regulator needs these inline; they are the entire point
    of the message."""
    import ssl  # noqa: PLC0415

    msg = EmailMessage()
    msg["From"] = from_address
    msg["To"] = to
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg.set_content(body)
    for filename, data, mime in attachments or []:
        # Split the mime "type/subtype" → set_content's add_attachment
        # takes maintype + subtype separately.
        m = (mime or "application/octet-stream").split("/", 1)
        if len(m) == 2:
            maintype, subtype = m
        else:
            maintype, subtype = "application", "octet-stream"
        msg.add_attachment(
            data, maintype=maintype, subtype=subtype, filename=filename
        )
    if port == 465 and not use_tls:
        # Implicit SSL — connect with SMTP_SSL.
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=ctx) as s:
            s.login(from_address, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port or 587) as s:
            if use_tls:
                s.starttls()
            s.login(from_address, password)
            s.send_message(msg)
    return msg.get("Message-ID") or ""


async def send_via_client_smtp(
    db: AsyncSession,
    *,
    client_id,
    actor: str,
    to: str,
    subject: str,
    body: str,
    cc: list[str] | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> tuple[bool, str | None, str | None]:
    """Send a filing email FROM THE CLIENT's address using their stored
    `client_email` credential. Returns (sent, message_id, error_text).

    If no client email credential is on file, returns (False, None,
    'No client email credential on file...') so the UI can prompt the
    operator to add one. We do NOT silently fall back to platform
    SMTP — that would put Switchboard on the From line of a regulated
    filing, which is wrong for chain-of-custody."""
    from app.vault import get_client_email_credential  # noqa: PLC0415

    resolved = await get_client_email_credential(db, client_id, actor=actor)
    if resolved is None:
        return False, None, (
            "No client email credential on file. Add a 'client_email' "
            "credential in the client's vault (username = client email "
            "address, secret = SMTP / app password). Filings are sent "
            "FROM the client, not from Switchboard."
        )
    from_address, password, host, port, use_tls = resolved
    try:
        mid = await asyncio.to_thread(
            _send_via_client_sync,
            from_address=from_address,
            password=password,
            host=host,
            port=port,
            use_tls=use_tls,
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            attachments=attachments,
        )
        return True, mid or None, None
    except Exception as exc:  # noqa: BLE001 — external SMTP boundary
        return False, None, (
            f"SMTP send to {host}:{port} failed — "
            f"{exc.__class__.__name__}: {exc}"
        )


async def send_email(
    db: AsyncSession, to: str, subject: str, body: str
) -> None:
    """Fire-and-forget send. Failures are logged and swallowed so the
    caller's workflow is never broken by an SMTP misconfiguration."""
    cfg = await _load_config(db)
    if not cfg.enabled:
        return
    try:
        await asyncio.to_thread(_send_sync, cfg, to, subject, body)
    except Exception:
        log.exception("Email delivery to %s failed (subject: %s)", to, subject)


async def notify_approval_queued(
    *,
    db: AsyncSession,
    to: str,
    client_name: str,
    action_type: str,
    tier: str,
    approval_id: str,
) -> None:
    base = (await platform_config.app_base_url(db)).rstrip("/")
    link = f"{base}/approvals" if base else "/approvals"
    subject = f"[Switchboard] {client_name}: {action_type} needs approval ({tier})"
    body = (
        f"An agent queued an action for {client_name} that requires your "
        f"approval.\n\n"
        f"Action: {action_type}\n"
        f"Tier:   {tier}\n"
        f"ID:     {approval_id}\n\n"
        f"Review and decide in the approval queue:\n  {link}\n"
    )
    await send_email(db, to, subject, body)
