"""Email notifications are best-effort: silent no-op when SMTP isn't
configured, single message sent when it is, and SMTP failures never
break the caller's workflow. Config is loaded from the platform_settings
DB rows with env fallback so the operator's GUI edits take effect on
the next call (no API restart)."""

from unittest.mock import patch

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import platform_config
from app.config import settings
from app.db import Base
from app.notifications import notify_approval_queued, send_email


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    saved_env = (
        settings.smtp_host, settings.smtp_from, settings.smtp_password,
        settings.app_base_url,
    )
    settings.smtp_host = ""
    settings.smtp_from = ""
    settings.smtp_password = ""
    settings.app_base_url = ""
    async with maker() as s:
        yield s
    (
        settings.smtp_host, settings.smtp_from, settings.smtp_password,
        settings.app_base_url,
    ) = saved_env
    await engine.dispose()


async def test_no_op_when_unconfigured(db: AsyncSession):
    with patch("app.notifications._send_sync") as send:
        await send_email(db, "op@example.com", "subject", "body")
        send.assert_not_called()


async def test_send_uses_db_values_over_env(db: AsyncSession):
    await platform_config.set_value(db, platform_config.KEY_SMTP_HOST, "db.example.com")
    await platform_config.set_value(
        db, platform_config.KEY_SMTP_FROM, "noreply@switchboard.test"
    )
    await platform_config.set_value(
        db, platform_config.KEY_APP_BASE_URL, "https://switchboard.example.com"
    )
    await db.commit()
    with patch("app.notifications._send_sync") as send:
        await notify_approval_queued(
            db=db,
            to="op@example.com",
            client_name="Acme",
            action_type="request_portal_action",
            tier="T3",
            approval_id="abc-123",
        )
    send.assert_called_once()
    cfg, to, subject, body = send.call_args.args
    assert cfg.host == "db.example.com"
    assert cfg.sender == "noreply@switchboard.test"
    assert to == "op@example.com"
    assert "Acme" in subject
    assert "request_portal_action" in subject
    assert "https://switchboard.example.com/approvals" in body
    assert "T3" in body
    assert "abc-123" in body


async def test_smtp_failure_is_swallowed(db: AsyncSession):
    await platform_config.set_value(db, platform_config.KEY_SMTP_HOST, "smtp.example.com")
    await platform_config.set_value(
        db, platform_config.KEY_SMTP_FROM, "noreply@switchboard.test"
    )
    await db.commit()

    def raise_error(*_a, **_kw):
        raise RuntimeError("DNS down")

    with patch("app.notifications._send_sync", side_effect=raise_error):
        # If send_email lets this propagate, the agent loop would die.
        await send_email(db, "op@example.com", "subject", "body")  # no raise


async def test_secrets_are_encrypted_at_rest(db: AsyncSession):
    """The DB row for a secret key must hold ciphertext, not the plaintext."""
    from sqlalchemy import select

    from app.models import PlatformSetting

    canary = "LEAK-CANARY-PASSWORD"
    await platform_config.set_value(
        db, platform_config.KEY_SMTP_PASSWORD, canary
    )
    await db.commit()
    row = await db.scalar(
        select(PlatformSetting).where(
            PlatformSetting.key == platform_config.KEY_SMTP_PASSWORD
        )
    )
    assert row.is_secret is True
    assert canary not in (row.value or "")
    # But the getter decrypts cleanly.
    got = await platform_config.smtp_password(db)
    assert got == canary
