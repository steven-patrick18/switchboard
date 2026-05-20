"""Live activity feed: what agents are doing right now, what they've
claimed, and the most recent runs. Operator-scoped — a fresh operator
sees nothing from another's workspace. In-memory DB, no API spend.
"""

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import AgentRun, Application, Project, Task


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


async def test_activity_empty_for_new_operator(http):
    c, _ = http
    reg = await c.post(
        "/auth/register",
        json={"email": "fresh@acme.com", "password": "supersecret", "name": "Fresh"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}

    r = await c.get("/activity", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["running_now"] == []
    assert body["awaiting_approval"] == []
    assert body["current_assignments"] == []
    assert body["recent_runs"] == []
    assert body["totals"]["running"] == 0
    assert body["totals"]["cost_last_24h"] == 0


async def test_activity_surfaces_running_assignments_and_recent_runs(http):
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op@acme.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}

    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    )

    # Seed a running task, an awaiting-approval task, a claimed application,
    # and a recent agent run — exercising all four feeds of /activity.
    async with maker() as s:
        p = Project(client_id=cid)
        s.add(p)
        await s.flush()

        running = Task(
            project_id=p.id,
            agent="carrier",
            status="running",
            input={"instruction": "Draft NECA-OCN-2 package"},
        )
        awaiting = Task(
            project_id=p.id,
            agent="compliance",
            status="awaiting_approval",
            input={"instruction": "Queue FCC 499 filing"},
        )
        s.add_all([running, awaiting])
        await s.flush()

        s.add(
            Application(
                client_id=cid,
                type="ocn",
                stage="in_progress",
                current_agent="carrier",
                notes="Drafting NECA-OCN-2 with attached LOA",
            )
        )
        s.add(
            AgentRun(
                agent="carrier",
                task_id=running.id,
                tokens_in=500,
                tokens_out=200,
                cost=0.04,
                duration_ms=8500,
            )
        )
        await s.commit()

    r = await c.get("/activity", headers=h)
    assert r.status_code == 200
    body = r.json()

    assert len(body["running_now"]) == 1
    assert body["running_now"][0]["agent"] == "carrier"
    assert "NECA-OCN-2" in body["running_now"][0]["instruction"]
    assert body["running_now"][0]["client_name"] == "Acme"

    assert len(body["awaiting_approval"]) == 1
    assert body["awaiting_approval"][0]["agent"] == "compliance"

    assert len(body["current_assignments"]) == 1
    a = body["current_assignments"][0]
    assert a["application_type"] == "ocn"
    assert a["application_label"] == "OCN (NECA)"
    assert a["current_agent"] == "carrier"
    assert a["stage"] == "in_progress"

    assert len(body["recent_runs"]) == 1
    run = body["recent_runs"][0]
    assert run["agent"] == "carrier"
    assert run["tokens_in"] == 500
    assert run["tokens_out"] == 200
    assert run["cost"] == 0.04
    assert run["client_name"] == "Acme"

    t = body["totals"]
    assert t["running"] == 1
    assert t["awaiting_approval"] == 1
    assert t["assignments_active"] == 1
    assert t["runs_last_24h"] == 1
    assert t["cost_last_24h"] == 0.04


async def test_activity_is_operator_scoped(http):
    c, maker = http
    a_reg = await c.post(
        "/auth/register",
        json={"email": "a@acme.com", "password": "supersecret", "name": "A"},
    )
    ha = {"Authorization": f"Bearer {a_reg.json()['access_token']}"}
    a_cid = uuid.UUID(
        (await c.post("/clients", headers=ha, json={"name": "A's client"})).json()["id"]
    )
    async with maker() as s:
        p = Project(client_id=a_cid)
        s.add(p)
        await s.flush()
        t = Task(project_id=p.id, agent="carrier", status="running", input={})
        s.add(t)
        await s.commit()

    # Operator B should not see operator A's running tasks.
    b_reg = await c.post(
        "/auth/register",
        json={"email": "b@acme.com", "password": "supersecret", "name": "B"},
    )
    hb = {"Authorization": f"Bearer {b_reg.json()['access_token']}"}
    r = await c.get("/activity", headers=hb)
    assert r.status_code == 200
    assert r.json()["running_now"] == []
    assert r.json()["totals"]["running"] == 0
