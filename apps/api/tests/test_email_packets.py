"""Per-filing email packets: when an approved filing is executed, the
operator gets a ready-to-send email (To/From/Subject/Body). They can
either one-click Send via SMTP or copy-paste manually. Inbound replies
are recorded via /approvals/{id}/record-reply which spawns a fresh
agent task with the reply text as context.
"""

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.email_packets import build_email_packet
from app.main import app
from app.models import Approval, ClientIntake, Project, Task
from app.models.approval import DECISION_PENDING, TIER_SIGN_PAY


# --- Pure unit coverage of the packet builder ------------------------


def test_neca_ocn_packet_targets_neca():
    pkt = build_email_packet(
        form="NECA-OCN-2",
        payload={},
        legal_name="Amano Telecom LLC",
        ein="39-2196239",
        from_address="op@switchboard.test",
    )
    assert pkt.to == "ocn-admin@neca.org"
    assert "Amano Telecom LLC" in pkt.subject
    assert "39-2196239" in pkt.subject
    assert "NECA-OCN-2" in pkt.subject
    assert "Letter of Agency" in (pkt.attachments_note or "")
    assert pkt.from_address == "op@switchboard.test"


def test_fcc_499_packet_uses_usac_inbox():
    pkt = build_email_packet(
        form="FCC 499-A",
        payload={"frn": "0392196239"},
        legal_name="Amano Telecom LLC",
        ein="39-2196239",
        from_address="op@x.test",
    )
    assert "usac" in pkt.to.lower()
    assert "499-A" in pkt.subject
    assert "0392196239" in pkt.body


def test_state_cpcn_packet_emits_state_placeholder():
    pkt = build_email_packet(
        form="state_cpcn:WY",
        payload={},
        legal_name="Amano Telecom LLC",
        ein="39-2196239",
        from_address="op@x.test",
    )
    # The PUC inbox is per-state and not in the catalog — leave it as
    # a TBD placeholder so the operator must explicitly override.
    assert pkt.to.startswith("[")
    assert "WY" in pkt.to
    assert "CPCN" in pkt.subject


def test_generic_fallback_for_unknown_forms():
    pkt = build_email_packet(
        form="UnknownForm-X",
        payload={},
        legal_name="Acme",
        ein="11-1234567",
        from_address="op@x.test",
        summary="Random filing",
    )
    assert pkt.to.startswith("[")  # placeholder — operator must override
    assert "UnknownForm-X" in pkt.subject
    assert "Random filing" in pkt.body


# --- HTTP coverage: send-email + record-reply -----------------------


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


async def _seed_approved_with_packet(maker, client_id: uuid.UUID) -> uuid.UUID:
    """Seed an executed approval that already carries an email packet —
    i.e. the executor ran and populated approval.email_packet."""
    async with maker() as s:
        s.add(
            ClientIntake(
                client_id=client_id,
                legal_name="Amano Telecom LLC",
                ein="39-2196239",
            )
        )
        p = Project(client_id=client_id)
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
                "from_address": "op@switchboard.test",
                "subject": "NECA-OCN-2 application: Amano Telecom LLC",
                "body": "Dear NECA, …",
                "cc": None,
                "attachments_note": "Attach: NECA-OCN-2.pdf, LOA.",
            },
        )
        s.add(appr)
        await s.flush()
        aid = appr.id
        await s.commit()
        return aid


async def test_send_email_400_when_smtp_not_configured(http):
    """No SMTP host/from → endpoint refuses with a clear error so the
    UI can fall back to the copy-paste path."""
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op@a.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Amano"})).json()["id"]
    )
    aid = await _seed_approved_with_packet(maker, cid)

    r = await c.post(f"/approvals/{aid}/send-email", headers=h, json={})
    assert r.status_code == 200
    body = r.json()
    # SMTP isn't configured in this in-memory test workspace → sent=False
    # with an explanatory error. The endpoint itself returns 200; the
    # UI surfaces the error.
    assert body["sent"] is False
    assert "SMTP" in (body["error"] or "")


async def test_send_email_rejects_placeholder_recipient(http):
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op2@a.com", "password": "supersecret", "name": "Op2"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Amano2"})).json()["id"]
    )
    aid = await _seed_approved_with_packet(maker, cid)

    # Force-feed a placeholder recipient (e.g. unfilled [TBD] from the
    # generic packet path) — endpoint rejects so the email never goes
    # out to literally '[recipient — TBD]'.
    r = await c.post(
        f"/approvals/{aid}/send-email",
        headers=h,
        json={"to": "[recipient — TBD]"},
    )
    assert r.status_code == 400
    assert "placeholder" in r.json()["detail"].lower()


async def test_record_reply_creates_new_task(http):
    """Operator pastes NECA's reply text → we spawn a new agent task on
    the same project with the reply baked into the instruction."""
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op3@a.com", "password": "supersecret", "name": "Op3"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Amano3"})).json()["id"]
    )
    aid = await _seed_approved_with_packet(maker, cid)

    r = await c.post(
        f"/approvals/{aid}/record-reply",
        headers=h,
        json={
            "reply": "NECA: your OCN application has been received. Docket #12345.",
            "from_address": "ocn-admin@neca.org",
        },
    )
    assert r.status_code == 200
    body = r.json()
    new_task_id = uuid.UUID(body["new_task_id"])
    # No Anthropic key in this workspace → task lands queued.
    assert body["ran"] is False
    async with maker() as s:
        t = await s.get(Task, new_task_id)
        assert t is not None
        assert t.status == "queued"
        assert t.agent == "carrier"
        instr = (t.input or {}).get("instruction", "")
        assert "INBOUND REPLY received" in instr
        assert "ocn-admin@neca.org" in instr
        assert "Docket #12345" in instr


async def test_record_reply_rejects_blank(http):
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op4@a.com", "password": "supersecret", "name": "Op4"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Amano4"})).json()["id"]
    )
    aid = await _seed_approved_with_packet(maker, cid)

    r = await c.post(
        f"/approvals/{aid}/record-reply", headers=h, json={"reply": "   "}
    )
    assert r.status_code == 422
