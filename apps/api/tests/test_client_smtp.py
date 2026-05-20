"""Outbound filings must be sent FROM THE CLIENT — not from Switchboard.
Per-client email credentials live in the vault; the platform decrypts
them at send time, opens the client's SMTP, and authenticates as the
client. Regulators reply to whoever sent the mail, so the From line
matters for chain-of-custody."""

import uuid
from unittest.mock import patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Approval, ClientIntake, Project, Task
from app.models.approval import DECISION_PENDING, TIER_SIGN_PAY
from app.vault import (
    _infer_smtp_from_email,
    _parse_smtp_url,
    get_client_email_credential,
    store_credential,
)


# --- Unit: SMTP host parsing + inference --------------------------------


def test_parse_smtp_url_bare_host():
    assert _parse_smtp_url("smtp.gmail.com") == ("smtp.gmail.com", 587, True)


def test_parse_smtp_url_with_port():
    assert _parse_smtp_url("smtp.gmail.com:587") == ("smtp.gmail.com", 587, True)


def test_parse_smtp_url_with_ssl_port_465():
    # 465 = implicit SSL; we connect via SMTP_SSL (use_tls flag flips off)
    assert _parse_smtp_url("smtp.gmail.com:465") == ("smtp.gmail.com", 465, False)


def test_parse_smtp_url_with_scheme():
    assert _parse_smtp_url("smtps://smtp.gmail.com:465") == (
        "smtp.gmail.com",
        465,
        False,
    )
    assert _parse_smtp_url("smtp://smtp.gmail.com:587") == (
        "smtp.gmail.com",
        587,
        True,
    )


def test_parse_smtp_url_empty():
    assert _parse_smtp_url(None) is None
    assert _parse_smtp_url("   ") is None


def test_infer_smtp_from_email_known_providers():
    assert _infer_smtp_from_email("amber@gmail.com") == ("smtp.gmail.com", 587, True)
    assert _infer_smtp_from_email("amber@outlook.com") == (
        "smtp-mail.outlook.com",
        587,
        True,
    )
    assert _infer_smtp_from_email("amber@icloud.com") == (
        "smtp.mail.me.com",
        587,
        True,
    )


def test_infer_smtp_from_email_unknown_provider():
    assert _infer_smtp_from_email("amber@randomcorp.example") is None


# --- DB-backed: credential resolution -----------------------------------


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
    async with maker() as session:
        yield session
    await engine.dispose()


async def _seed_client(db: AsyncSession) -> uuid.UUID:
    from app.models import Client  # noqa: PLC0415
    from app.models.user import User  # noqa: PLC0415

    u = User(email="op@a.com", hashed_password="x", name="Op")
    db.add(u)
    await db.flush()
    c = Client(owner_id=u.id, name="Amano Telecom LLC")
    db.add(c)
    await db.flush()
    return c.id


async def test_get_client_email_credential_returns_none_when_missing(db):
    cid = await _seed_client(db)
    out = await get_client_email_credential(db, cid, actor="op@a.com")
    assert out is None


async def test_get_client_email_credential_resolves_with_explicit_host(db):
    cid = await _seed_client(db)
    await store_credential(
        db,
        client_id=cid,
        service="client_email",
        username="amber@amano.test",
        secret="app-password-here",
        url="smtp.custom-host.test:465",
    )
    out = await get_client_email_credential(db, cid, actor="op@a.com")
    assert out is not None
    from_address, password, host, port, use_tls = out
    assert from_address == "amber@amano.test"
    assert password == "app-password-here"
    assert host == "smtp.custom-host.test"
    assert port == 465
    assert use_tls is False  # implicit SSL on 465


async def test_get_client_email_credential_infers_from_gmail_domain(db):
    """If the operator stores the email + app-password but leaves URL
    blank, the resolver infers the SMTP server from the email domain
    so the operator doesn't have to look it up."""
    cid = await _seed_client(db)
    await store_credential(
        db,
        client_id=cid,
        service="client_email",
        username="amber@gmail.com",
        secret="googleapp-pw",
        url=None,
    )
    out = await get_client_email_credential(db, cid, actor="op@a.com")
    assert out is not None
    _, _, host, port, use_tls = out
    assert host == "smtp.gmail.com"
    assert port == 587
    assert use_tls is True


async def test_get_client_email_credential_rejects_unknown_domain_without_url(db):
    """No URL + unknown email domain → resolver returns None so the UI
    surfaces 'add the SMTP host' rather than silently failing on
    send."""
    cid = await _seed_client(db)
    await store_credential(
        db,
        client_id=cid,
        service="client_email",
        username="amber@privatecorp.example",
        secret="pw",
        url=None,
    )
    out = await get_client_email_credential(db, cid, actor="op@a.com")
    assert out is None


# --- HTTP: send-email uses client path by default -----------------------


@pytest_asyncio.fixture
async def http():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = _override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c, maker
    app.dependency_overrides.clear()
    await engine.dispose()


async def _seed_for_send(maker, owner_email: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Returns (client_id, approval_id) — a client with intake, an
    approval with an email_packet, and a project/task wired through.
    No SMTP credentials seeded by default."""
    async with maker() as s:
        from app.models import Client  # noqa: PLC0415
        from app.models.user import User  # noqa: PLC0415

        u = await s.scalar(
            __import__("sqlalchemy").select(User).where(User.email == owner_email)
        )
        cl = Client(owner_id=u.id, name="Amano")
        s.add(cl)
        await s.flush()
        s.add(
            ClientIntake(
                client_id=cl.id,
                legal_name="Amano Telecom LLC",
                ein="39-2196239",
                officer_email="amber@amano.test",
            )
        )
        p = Project(client_id=cl.id)
        s.add(p)
        await s.flush()
        t = Task(
            project_id=p.id,
            agent="carrier",
            status="awaiting_approval",
            input={"instruction": "Draft NECA-OCN-2"},
        )
        s.add(t)
        await s.flush()
        appr = Approval(
            task_id=t.id,
            action_type="queue_filing_submission",
            tier=TIER_SIGN_PAY,
            payload={"form": "NECA-OCN-2"},
            decision=DECISION_PENDING,
            email_packet={
                "to": "ocn-admin@neca.org",
                "from_address": "amber@amano.test",
                "subject": "NECA-OCN-2: Amano Telecom LLC",
                "body": "Dear NECA, …",
                "cc": None,
                "attachments_note": "Attach: NECA-OCN-2.pdf",
            },
        )
        s.add(appr)
        await s.flush()
        out = (cl.id, appr.id)
        await s.commit()
        return out


async def test_send_email_via_client_path_errors_without_credential(http):
    """Default `via='client'` path. With no client_email credential on
    file, the endpoint refuses with a clear instruction to add one
    rather than silently sending from Switchboard."""
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op@a.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    _, aid = await _seed_for_send(maker, "op@a.com")

    r = await c.post(f"/approvals/{aid}/send-email", headers=h, json={})
    assert r.status_code == 200
    body = r.json()
    assert body["sent"] is False
    assert "client_email" in (body["error"] or "")
    assert "vault" in (body["error"] or "")


async def test_send_email_via_client_path_succeeds_when_smtp_send_mocked(http):
    """With a client_email credential AND the SMTP send patched (we
    obviously don't open a real SMTP connection in CI), the endpoint
    records sent_at + message_id and audits the send under the client
    sender path."""
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op2@a.com", "password": "supersecret", "name": "Op2"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid, aid = await _seed_for_send(maker, "op2@a.com")

    async with maker() as s:
        await store_credential(
            s,
            client_id=cid,
            service="client_email",
            username="amber@gmail.com",  # Gmail → SMTP inferred
            secret="googleapp-pw",
        )
        await s.commit()

    # Patch the synchronous SMTP send so we never touch the network.
    with patch(
        "app.notifications._send_via_client_sync",
        return_value="<test-msg-id@switchboard.test>",
    ) as mocked:
        r = await c.post(f"/approvals/{aid}/send-email", headers=h, json={})
    assert r.status_code == 200
    body = r.json()
    assert body["sent"] is True
    assert body["message_id"] == "<test-msg-id@switchboard.test>"
    # And the actual SMTP call was made with the CLIENT's address as From.
    assert mocked.called
    kwargs = mocked.call_args.kwargs
    assert kwargs["from_address"] == "amber@gmail.com"
    assert kwargs["host"] == "smtp.gmail.com"
    assert kwargs["port"] == 587
    assert kwargs["to"] == "ocn-admin@neca.org"

    # sent_at + message_id recorded on the approval.
    async with maker() as s:
        a = await s.get(Approval, aid)
        assert a.email_sent_at is not None
        assert a.email_message_id == "<test-msg-id@switchboard.test>"
