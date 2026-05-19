"""Email notifications are best-effort: silent no-op when SMTP isn't
configured, single message sent when it is, and SMTP failures never
break the caller's workflow.
"""

from unittest.mock import patch

import pytest

from app.config import settings
from app.notifications import notify_approval_queued, send_email


@pytest.fixture
def smtp_settings():
    """Patch SMTP settings and restore on teardown."""
    saved = {
        k: getattr(settings, k)
        for k in (
            "smtp_host",
            "smtp_port",
            "smtp_user",
            "smtp_password",
            "smtp_from",
            "smtp_use_tls",
            "app_base_url",
        )
    }
    yield saved
    for k, v in saved.items():
        setattr(settings, k, v)


async def test_no_op_when_unconfigured(smtp_settings):
    settings.smtp_host = ""
    settings.smtp_from = ""
    with patch("app.notifications._send_sync") as send:
        await send_email("op@example.com", "subject", "body")
        send.assert_not_called()


async def test_send_when_configured(smtp_settings):
    settings.smtp_host = "smtp.example.com"
    settings.smtp_from = "noreply@switchboard.test"
    settings.app_base_url = "https://switchboard.example.com"
    with patch("app.notifications._send_sync") as send:
        await notify_approval_queued(
            to="op@example.com",
            client_name="Acme",
            action_type="request_portal_action",
            tier="T3",
            approval_id="abc-123",
        )
    send.assert_called_once()
    args = send.call_args.args
    assert args[0] == "op@example.com"
    assert "Acme" in args[1]
    assert "request_portal_action" in args[1]
    # Body includes the link.
    assert "https://switchboard.example.com/approvals" in args[2]
    assert "T3" in args[2]
    assert "abc-123" in args[2]


async def test_smtp_failure_is_swallowed(smtp_settings):
    settings.smtp_host = "smtp.example.com"
    settings.smtp_from = "noreply@switchboard.test"

    def raise_error(*_a, **_kw):
        raise RuntimeError("DNS down")

    # If send_email lets this propagate, the agent loop would die. The
    # contract: notification failures must never break the caller.
    with patch("app.notifications._send_sync", side_effect=raise_error):
        await send_email("op@example.com", "subject", "body")  # no raise
