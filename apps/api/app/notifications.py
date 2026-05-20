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


def _send_sync(cfg: SmtpConfig, to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = cfg.sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(cfg.host, cfg.port or 587) as s:
        if cfg.use_tls:
            s.starttls()
        if cfg.user and cfg.password:
            s.login(cfg.user, cfg.password)
        s.send_message(msg)


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
