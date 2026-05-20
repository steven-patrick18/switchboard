"""Backfill path: re-run PDF generation against an already-decided
approval's saved payload, populating Approval.email_packet.attachments
without touching the agent loop. Operators need this for approvals
decided BEFORE v1.3.18 added PDF generation — their email_packet has
no attachments, and the Document Hub shows "no file" stubs.
"""

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Approval, ClientIntake, Document, Project, Task
from app.models.approval import (
    DECISION_APPROVED,
    DECISION_PENDING,
    TIER_APPROVE,
    TIER_SIGN_PAY,
)


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


async def _seed_legacy_approval(maker, client_id: uuid.UUID) -> uuid.UUID:
    """A decided approval that pre-dates v1.3.18 — has an email_packet
    (because v1.3.14 built one) but no `attachments` array on it,
    matching what's actually in production right now."""
    async with maker() as s:
        s.add(
            ClientIntake(
                client_id=client_id,
                legal_name="Amano Telecom LLC",
                ein="39-2196239",
                officer_email="amber@amanotelecom.com",
            )
        )
        p = Project(client_id=client_id)
        s.add(p)
        await s.flush()
        t = Task(
            project_id=p.id,
            agent="carrier",
            status="completed",
            input={"instruction": "Draft NECA-OCN-2"},
        )
        s.add(t)
        await s.flush()
        appr = Approval(
            task_id=t.id,
            action_type="queue_filing_submission",
            tier=TIER_SIGN_PAY,
            payload={
                "form": "NECA-OCN-2",
                "applicant_legal_name": "Amano Telecom LLC",
                "ein": "39-2196239",
                "frn": "0037045218",
                "letter_of_agency": {
                    "grantor": "Amano Telecom LLC",
                    "grantee": "Switchboard",
                    "signatory_name": "AMBER SIDNEY HUNT",
                    "effective_date": "2026-05-20",
                },
            },
            decision=DECISION_APPROVED,
            # Legacy packet — no `attachments` key yet.
            email_packet={
                "to": "ocn-admin@neca.org",
                "from_address": "amber@amanotelecom.com",
                "subject": "NECA-OCN-2 application: Amano Telecom LLC",
                "body": "Dear NECA, …",
                "cc": None,
                "attachments_note": "Attach: NECA-OCN-2.pdf, signed LOA.",
            },
        )
        s.add(appr)
        await s.flush()
        aid = appr.id
        await s.commit()
        return aid


async def test_regenerate_attachments_backfills_pdfs_on_legacy_approval(http):
    """The Amber Sidney Hunt scenario: 4 'no file' NECA-OCN-2 versions
    in the Document Hub. Click Regenerate PDFs → new Document rows
    with real bytes, email_packet.attachments populated, the To/
    Subject/Body the operator already tweaked is preserved."""
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op@a.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Amano"})).json()["id"]
    )
    aid = await _seed_legacy_approval(maker, cid)

    r = await c.post(
        f"/approvals/{aid}/regenerate-attachments", headers=h, json={}
    )
    assert r.status_code == 200
    body = r.json()
    # Email packet now carries 2 attachments (NECA-OCN-2 form + LOA).
    atts = (body.get("email_packet") or {}).get("attachments") or []
    assert len(atts) == 2
    assert {a["filename"].endswith(".pdf") for a in atts} == {True}
    # Pre-existing packet fields are preserved verbatim.
    packet = body["email_packet"]
    assert packet["to"] == "ocn-admin@neca.org"
    assert packet["from_address"] == "amber@amanotelecom.com"

    # New Document rows are real files now.
    from sqlalchemy import select  # noqa: PLC0415

    async with maker() as s:
        docs = (
            await s.scalars(select(Document).where(Document.client_id == cid))
        ).all()
    assert len(docs) == 2
    for d in docs:
        assert d.s3_key is not None
        assert d.size_bytes and d.size_bytes > 1000
        assert d.mime == "application/pdf"


async def test_regenerate_attachments_bumps_versions_each_call(http):
    """Idempotency-ish: calling regenerate twice produces two NEW
    Document versions, not duplicates of v1. The Document Hub keeps
    forensic history; the email_packet.attachments points at the
    LATEST generated set."""
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op2@a.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    )
    aid = await _seed_legacy_approval(maker, cid)

    await c.post(f"/approvals/{aid}/regenerate-attachments", headers=h, json={})
    await c.post(f"/approvals/{aid}/regenerate-attachments", headers=h, json={})

    from sqlalchemy import select  # noqa: PLC0415

    async with maker() as s:
        docs = (
            await s.scalars(select(Document).where(Document.client_id == cid))
        ).all()
    # Two generation runs × (form + LOA) = 4 Document rows total
    assert len(docs) == 4
    # Versions step from 1 -> 2 for each type
    by_type = {}
    for d in docs:
        by_type.setdefault(d.type, []).append(d.version)
    for vs in by_type.values():
        assert sorted(vs) == [1, 2]


async def test_regenerate_attachments_rejects_non_filing_approval(http):
    """Only filings carry mailable PDFs. Asking to regenerate against,
    say, a portal_action approval is meaningless — return a clear 400."""
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op3@a.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    )
    async with maker() as s:
        p = Project(client_id=cid)
        s.add(p)
        await s.flush()
        t = Task(project_id=p.id, agent="carrier", status="completed", input={})
        s.add(t)
        await s.flush()
        appr = Approval(
            task_id=t.id,
            action_type="request_portal_action",
            tier=TIER_APPROVE,
            payload={"service": "fcc_cores", "action": "check_filer_status"},
            decision=DECISION_APPROVED,
        )
        s.add(appr)
        await s.flush()
        aid = appr.id
        await s.commit()

    r = await c.post(
        f"/approvals/{aid}/regenerate-attachments", headers=h, json={}
    )
    assert r.status_code == 400
    assert "queue_filing_submission" in r.json()["detail"]


async def test_regenerate_attachments_404_for_other_operators(http):
    """Owner-scoped: B can't regenerate against A's approval."""
    c, maker = http
    a_reg = await c.post(
        "/auth/register",
        json={"email": "a@a.com", "password": "supersecret", "name": "A"},
    )
    ha = {"Authorization": f"Bearer {a_reg.json()['access_token']}"}
    a_cid = uuid.UUID(
        (await c.post("/clients", headers=ha, json={"name": "A's"})).json()["id"]
    )
    aid = await _seed_legacy_approval(maker, a_cid)

    b_reg = await c.post(
        "/auth/register",
        json={"email": "b@a.com", "password": "supersecret", "name": "B"},
    )
    hb = {"Authorization": f"Bearer {b_reg.json()['access_token']}"}
    r = await c.post(
        f"/approvals/{aid}/regenerate-attachments", headers=hb, json={}
    )
    assert r.status_code == 404


async def test_regenerate_attachments_rejects_pending_with_no_payload(http):
    """Defensive: an approval with no payload can't be regenerated."""
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op5@a.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    )
    async with maker() as s:
        p = Project(client_id=cid)
        s.add(p)
        await s.flush()
        t = Task(project_id=p.id, agent="carrier", status="awaiting_approval", input={})
        s.add(t)
        await s.flush()
        appr = Approval(
            task_id=t.id,
            action_type="queue_filing_submission",
            tier=TIER_SIGN_PAY,
            payload=None,
            decision=DECISION_PENDING,
        )
        s.add(appr)
        await s.flush()
        aid = appr.id
        await s.commit()

    r = await c.post(
        f"/approvals/{aid}/regenerate-attachments", headers=h, json={}
    )
    assert r.status_code == 400
    assert "payload" in r.json()["detail"].lower()
