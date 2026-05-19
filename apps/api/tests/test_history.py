"""History view filters on the approvals endpoint: `decision=decided`
(non-pending), `client_id`, `action_type`, with operator scoping.
"""

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Approval, Project, Task
from app.models.approval import (
    DECISION_APPROVED,
    DECISION_EDITED,
    DECISION_PENDING,
    DECISION_REJECTED,
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

    async def _odb():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = _odb
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c, maker
    app.dependency_overrides.clear()
    await engine.dispose()


async def _seed(
    maker,
    client_id: uuid.UUID,
    action_type: str,
    decision: str,
    tier: str = TIER_APPROVE,
) -> uuid.UUID:
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
            action_type=action_type,
            tier=tier,
            payload={"x": 1},
            decision=decision,
        )
        s.add(a)
        await s.flush()
        aid = a.id
        await s.commit()
        return aid


async def test_history_filters(http):
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op@acme.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    a = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    )
    b = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Beta"})).json()["id"]
    )

    # Acme: one pending, one approved (portal), one rejected (filing).
    p_acme = await _seed(maker, a, "request_portal_action", DECISION_PENDING)
    ap_acme = await _seed(
        maker, a, "request_portal_action", DECISION_APPROVED
    )
    rj_acme = await _seed(
        maker, a, "queue_filing_submission", DECISION_REJECTED, TIER_SIGN_PAY
    )
    # Beta: one edited (portal).
    ed_beta = await _seed(maker, b, "request_portal_action", DECISION_EDITED)

    # Default (decision=pending) — only the pending Acme row.
    pend = (await c.get("/approvals", headers=h)).json()
    assert {x["id"] for x in pend} == {str(p_acme)}

    # decision=decided → non-pending: 3 entries across both clients.
    dec = (await c.get("/approvals?decision=decided", headers=h)).json()
    assert {x["id"] for x in dec} == {str(ap_acme), str(rj_acme), str(ed_beta)}

    # Filter by client_id → only that client's items.
    only_acme = (
        await c.get(
            f"/approvals?decision=decided&client_id={a}", headers=h
        )
    ).json()
    assert {x["id"] for x in only_acme} == {str(ap_acme), str(rj_acme)}

    # Filter by action_type.
    only_portal = (
        await c.get(
            "/approvals?decision=decided&action_type=request_portal_action",
            headers=h,
        )
    ).json()
    assert {x["id"] for x in only_portal} == {str(ap_acme), str(ed_beta)}

    # Combined filters.
    combo = (
        await c.get(
            f"/approvals?decision=decided&client_id={a}"
            f"&action_type=request_portal_action",
            headers=h,
        )
    ).json()
    assert {x["id"] for x in combo} == {str(ap_acme)}

    # Operator isolation still holds.
    reg2 = await c.post(
        "/auth/register",
        json={"email": "z@acme.com", "password": "supersecret", "name": "Z"},
    )
    hz = {"Authorization": f"Bearer {reg2.json()['access_token']}"}
    assert (await c.get("/approvals?decision=decided", headers=hz)).json() == []
