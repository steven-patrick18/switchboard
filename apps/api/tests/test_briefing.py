"""Operator morning briefing (brief slide 8): a deterministic cross-client
digest — what's captured, what's blocked, what's waiting on you. In-memory
DB, no API spend.
"""

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import AgentRun, Approval, Project, Task
from app.models.approval import DECISION_PENDING, TIER_SIGN_PAY

_FULL_INTAKE = {
    "legal_name": "Acme VoIP LLC",
    "entity_type": "LLC",
    "formation_state": "DE",
    "ein": "99-1234567",
    "principal_address": {"street": "1 Main", "city": "Austin", "zip": "78701"},
    "officer_name": "Jane Roe",
    "officer_title": "CEO",
    "officer_email": "jane@acme.example",
    "primary_contact_name": "Jane Roe",
    "primary_contact_email": "jane@acme.example",
    "primary_contact_phone": "+15125550100",
    "target_states": ["TX"],
    "estimated_monthly_revenue": 25000,
}


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


async def test_briefing_aggregates_and_is_scoped(http):
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

    # Client A: fully captured — register exactly the docs the system
    # decided are mandated for this client's intake.
    st = (await c.put(f"/clients/{a}/intake", headers=h, json=_FULL_INTAKE)).json()
    for d in st["completeness"]["required_documents"]:
        await c.post(f"/clients/{a}/documents", headers=h, json={"type": d["key"]})
    # Client B: intake left empty (blocked).

    # Seed A: a task awaiting approval, a pending approval, an agent run.
    async with maker() as s:
        p = Project(client_id=a)
        s.add(p)
        await s.flush()
        t = Task(project_id=p.id, agent="compliance", status="awaiting_approval")
        s.add(t)
        await s.flush()
        s.add(
            Approval(
                task_id=t.id,
                action_type="queue_filing_submission",
                tier=TIER_SIGN_PAY,
                payload={"form": "FCC 499-A"},
                decision=DECISION_PENDING,
            )
        )
        s.add(
            AgentRun(
                agent="compliance",
                task_id=t.id,
                tokens_in=100,
                tokens_out=50,
                cost=0.25,
                duration_ms=10,
            )
        )
        await s.commit()

    r = await c.get("/briefing", headers=h)
    assert r.status_code == 200
    body = r.json()

    assert body["totals"]["clients"] == 2
    assert body["totals"]["pending_approvals"] == 1
    assert body["totals"]["agent_runs"] == 1
    assert body["totals"]["total_cost"] == 0.25
    assert body["totals"]["tasks_by_status"]["awaiting_approval"] == 1

    by_name = {cs["name"]: cs for cs in body["clients"]}
    assert by_name["Acme"]["intake_complete"] is True
    assert by_name["Acme"]["pending_approvals"] == 1
    assert by_name["Acme"]["open_tasks"] == 1
    assert by_name["Beta"]["intake_complete"] is False

    joined = " ".join(body["attention"])
    assert "waiting on you" in joined
    assert "Beta" in joined  # blocked client surfaced
    assert "2 client(s)" in body["summary"]

    # Workspace isolation: a fresh operator sees an empty book.
    reg2 = await c.post(
        "/auth/register",
        json={"email": "z@acme.com", "password": "supersecret", "name": "Z"},
    )
    hz = {"Authorization": f"Bearer {reg2.json()['access_token']}"}
    r = await c.get("/briefing", headers=hz)
    assert r.status_code == 200
    z = r.json()
    assert z["totals"]["clients"] == 0
    assert z["totals"]["pending_approvals"] == 0
    assert z["clients"] == []
