"""Approving an item now *does something*: it runs the in-platform
follow-through (materializes a versioned Document Hub record) and stamps
the result. Rejecting does not. No external I/O, no API spend.
"""

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.execution import execute_approval
from app.main import app  # registers metadata
from app.models import Approval, Credential, Document, Project, Task
from app.models.approval import (
    DECISION_APPROVED,
    DECISION_PENDING,
    TIER_APPROVE,
    TIER_SIGN_PAY,
)
from app.vault import store_credential


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
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_executors_create_versioned_documents(db: AsyncSession):
    cid = uuid.uuid4()

    a1 = Approval(
        task_id=uuid.uuid4(),
        action_type="queue_filing_submission",
        tier=TIER_SIGN_PAY,
        payload={"form": "FCC 499-A", "summary": "annual"},
        decision=DECISION_APPROVED,
    )
    db.add(a1)
    await db.flush()
    r1 = await execute_approval(a1, cid, db)
    assert "FCC 499-A (v1)" in r1

    # Same form again → version increments.
    a2 = Approval(
        task_id=uuid.uuid4(),
        action_type="queue_filing_submission",
        tier=TIER_SIGN_PAY,
        payload={"form": "FCC 499-A"},
        decision=DECISION_APPROVED,
    )
    db.add(a2)
    await db.flush()
    assert "FCC 499-A (v2)" in await execute_approval(a2, cid, db)

    a3 = Approval(
        task_id=uuid.uuid4(),
        action_type="send_document_for_signature",
        tier=TIER_SIGN_PAY,
        payload={"doc_type": "LOA", "recipient": "carrier@x.com"},
        decision=DECISION_APPROVED,
    )
    db.add(a3)
    await db.flush()
    r3 = await execute_approval(a3, cid, db)
    assert "LOA (v1)" in r3 and "Documenso" in r3

    # Unknown action → graceful, no document.
    a4 = Approval(
        task_id=uuid.uuid4(),
        action_type="mystery_action",
        tier=TIER_SIGN_PAY,
        payload={},
        decision=DECISION_APPROVED,
    )
    db.add(a4)
    await db.flush()
    assert "No executor registered" in await execute_approval(a4, cid, db)
    await db.commit()

    docs = (await db.scalars(select(Document).where(Document.client_id == cid))).all()
    assert sorted((d.type, d.version) for d in docs) == [
        ("FCC 499-A", 1),
        ("FCC 499-A", 2),
        ("LOA", 1),
    ]


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


async def _seed(maker, client_id) -> uuid.UUID:
    async with maker() as s:
        p = Project(client_id=client_id)
        s.add(p)
        await s.flush()
        t = Task(project_id=p.id, agent="compliance", status="awaiting_approval")
        s.add(t)
        await s.flush()
        a = Approval(
            task_id=t.id,
            action_type="queue_filing_submission",
            tier=TIER_SIGN_PAY,
            payload={"form": "FCC 499-A", "summary": "x"},
            decision=DECISION_PENDING,
        )
        s.add(a)
        await s.flush()
        aid = a.id
        await s.commit()
        return aid


async def test_approve_executes_reject_does_not(http):
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op@acme.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    )

    a_ok = await _seed(maker, cid)
    a_no = await _seed(maker, cid)

    r = await c.post(f"/approvals/{a_ok}/approve", headers=h, json={})
    assert r.status_code == 200
    body = r.json()
    assert body["executed_at"] is not None
    assert "Document Hub" in body["execution_result"]

    r = await c.post(f"/approvals/{a_no}/reject", headers=h, json={"reason": "no"})
    assert r.status_code == 200
    assert r.json()["executed_at"] is None
    assert r.json()["execution_result"] is None

    # Exactly one Document materialized (from the approved one only).
    docs = (await c.get(f"/clients/{cid}/documents", headers=h)).json()
    assert len(docs) == 1 and docs[0]["type"] == "FCC 499-A"


async def _seed_portal(maker, client_id, service="fcc_cores", action="submit_499_q") -> uuid.UUID:
    async with maker() as s:
        p = Project(client_id=client_id)
        s.add(p)
        await s.flush()
        t = Task(
            project_id=p.id, agent="compliance", status="awaiting_approval"
        )
        s.add(t)
        await s.flush()
        a = Approval(
            task_id=t.id,
            action_type="request_portal_action",
            tier=TIER_APPROVE,
            payload={"service": service, "action": action, "summary": "go"},
            decision=DECISION_PENDING,
        )
        s.add(a)
        await s.flush()
        aid = a.id
        await s.commit()
        return aid


async def test_portal_action_unit(db: AsyncSession):
    cid = uuid.uuid4()
    # No credential on file → graceful, no document.
    a1 = Approval(
        task_id=uuid.uuid4(),
        action_type="request_portal_action",
        tier=TIER_APPROVE,
        payload={
            "service": "fcc_cores",
            "action": "submit_499_q",
            "summary": "go",
        },
        decision=DECISION_APPROVED,
    )
    db.add(a1)
    await db.flush()
    result = await execute_approval(a1, cid, db)
    assert "no credential on file" in result
    assert (
        await db.scalars(select(Document).where(Document.client_id == cid))
    ).all() == []

    # Add the credential, then approve again → document materialized.
    await store_credential(
        db, client_id=cid, service="fcc_cores", secret="TOPSECRET"
    )
    a2 = Approval(
        task_id=uuid.uuid4(),
        action_type="request_portal_action",
        tier=TIER_APPROVE,
        payload={
            "service": "fcc_cores",
            "action": "submit_499_q",
            "summary": "go",
        },
        decision=DECISION_APPROVED,
    )
    db.add(a2)
    await db.flush()
    result2 = await execute_approval(a2, cid, db)
    await db.commit()
    assert "portal_action:fcc_cores:submit_499_q (v1)" in result2
    docs = (
        await db.scalars(select(Document).where(Document.client_id == cid))
    ).all()
    assert any(
        d.type == "portal_action:fcc_cores:submit_499_q" for d in docs
    )


async def test_portal_action_http_flow(http):
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "p@acme.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    )

    # No credential yet → approve produces the explicit message, no Document.
    a_no = await _seed_portal(maker, cid)
    r = await c.post(f"/approvals/{a_no}/approve", headers=h, json={})
    assert r.status_code == 200
    assert "no credential on file" in r.json()["execution_result"]
    assert (await c.get(f"/clients/{cid}/documents", headers=h)).json() == []

    # Store the credential via API; approve again → Document materialized.
    await c.post(
        f"/clients/{cid}/credentials",
        headers=h,
        json={"service": "fcc_cores", "secret": "TOPSECRET"},
    )
    a_ok = await _seed_portal(maker, cid)
    r = await c.post(f"/approvals/{a_ok}/approve", headers=h, json={})
    assert r.status_code == 200
    assert "portal_action:fcc_cores:submit_499_q" in r.json()["execution_result"]
    docs = (await c.get(f"/clients/{cid}/documents", headers=h)).json()
    assert any(
        d["type"] == "portal_action:fcc_cores:submit_499_q" for d in docs
    )
