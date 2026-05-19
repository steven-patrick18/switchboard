"""Best-effort SMTP notifications.

The platform never *blocks* on email — if SMTP isn't configured, or
delivery fails, the operator's UI flow proceeds unchanged. Email is a
convenience: the in-app pending-approval badge is the source of truth.

Configure via settings.smtp_host / smtp_port / smtp_user / smtp_password
/ smtp_from / smtp_use_tls (all empty by default → no-op).
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

log = logging.getLogger(__name__)


def _enabled() -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


def _send_sync(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port or 587) as s:
        if settings.smtp_use_tls:
            s.starttls()
        if settings.smtp_user and settings.smtp_password:
            s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(msg)


async def send_email(to: str, subject: str, body: str) -> None:
    """Fire-and-forget send. Failures are logged and swallowed so the
    caller's workflow is never broken by an SMTP misconfiguration."""
    if not _enabled():
        return
    try:
        await asyncio.to_thread(_send_sync, to, subject, body)
    except Exception:
        log.exception("Email delivery to %s failed (subject: %s)", to, subject)


async def notify_approval_queued(
    *,
    to: str,
    client_name: str,
    action_type: str,
    tier: str,
    approval_id: str,
) -> None:
    base = settings.app_base_url.rstrip("/") if settings.app_base_url else ""
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
    await send_email(to, subject, body)
