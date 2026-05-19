"""The approval queue is the product. This drives the full HTTP surface:
operator-scoped listing, approve / edit&approve / reject, the pending-only
guard, batch sweep, and workspace isolation. In-memory DB, no API spend.
"""

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app  # importing app.main registers all model metadata
from app.models import Approval, Project, Task
from app.models.approval import DECISION_PENDING, TIER_SIGN_PAY


@pytest_asyncio.fixture
async def ctx():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, maker
    app.dependency_overrides.clear()
    await engine.dispose()


async def _seed_approval(maker, client_id: uuid.UUID, **kw) -> uuid.UUID:
    async with maker() as s:
        project = Project(client_id=client_id)
        s.add(project)
        await s.flush()
        task = Task(project_id=project.id, agent="compliance", status="awaiting_approval")
        s.add(task)
        await s.flush()
        appr = Approval(
            task_id=task.id,
            action_type=kw.get("action_type", "queue_filing_submission"),
            tier=TIER_SIGN_PAY,
            payload=kw.get("payload", {"form": "FCC 499-A"}),
            decision=DECISION_PENDING,
        )
        s.add(appr)
        await s.flush()
        aid = appr.id
        await s.commit()
        return aid


async def test_approval_queue(ctx):
    c, maker = ctx

    reg = await c.post(
        "/auth/register",
        json={"email": "op@example.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    me = (await c.get("/auth/me", headers=h)).json()
    cid = uuid.UUID((await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"])

    a1 = await _seed_approval(maker, cid)
    a2 = await _seed_approval(maker, cid)

    # Queue lists pending items with client context.
    r = await c.get("/approvals", headers=h)
    assert r.status_code == 200
    items = r.json()
    assert {i["id"] for i in items} == {str(a1), str(a2)}
    assert all(i["client_name"] == "Acme" and i["decision"] == "pending" for i in items)

    # Edit & approve a1.
    r = await c.post(
        f"/approvals/{a1}/approve",
        headers=h,
        json={"payload_override": {"form": "FCC 499-A", "fixed": True}, "note": "tweaked"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "edited"
    assert body["payload"] == {"form": "FCC 499-A", "fixed": True}
    assert body["reviewer_id"] == me["id"]
    assert body["note"] == "tweaked"

    # Pending-only guard.
    r = await c.post(f"/approvals/{a1}/approve", headers=h, json={})
    assert r.status_code == 409

    # Reject a2 with a reason.
    r = await c.post(
        f"/approvals/{a2}/reject", headers=h, json={"reason": "wrong tone"}
    )
    assert r.status_code == 200
    assert r.json()["decision"] == "rejected"
    assert r.json()["note"] == "wrong tone"

    # No pending left; ?decision=all shows both.
    assert (await c.get("/approvals", headers=h)).json() == []
    assert len((await c.get("/approvals?decision=all", headers=h)).json()) == 2

    # Batch sweep: 3 new pending + the already-edited a1 (skipped).
    b1 = await _seed_approval(maker, cid)
    b2 = await _seed_approval(maker, cid)
    b3 = await _seed_approval(maker, cid)
    r = await c.post(
        "/approvals/batch",
        headers=h,
        json={"ids": [str(b1), str(b2), str(b3), str(a1)], "decision": "approved"},
    )
    assert r.status_code == 200
    res = r.json()
    assert set(res["updated"]) == {str(b1), str(b2), str(b3)}
    assert res["skipped"] == [str(a1)]

    # Workspace isolation.
    reg2 = await c.post(
        "/auth/register",
        json={"email": "b@example.com", "password": "supersecret", "name": "B"},
    )
    hb = {"Authorization": f"Bearer {reg2.json()['access_token']}"}
    assert (await c.get("/approvals?decision=all", headers=hb)).json() == []
    assert (await c.get(f"/approvals/{b1}", headers=hb)).status_code == 404
    assert (await c.post(f"/approvals/{b1}/reject", headers=hb, json={})).status_code == 404
