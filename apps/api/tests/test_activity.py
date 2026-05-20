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
from app.models import AgentRun, Application, Approval, Project, Task
from app.models.approval import DECISION_PENDING, TIER_SIGN_PAY


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

        # Pending approval ON the awaiting task — this is what /activity
        # now sources from (Approval.decision='pending', not just
        # Task.status='awaiting_approval').
        s.add(
            Approval(
                task_id=awaiting.id,
                action_type="queue_filing_submission",
                tier=TIER_SIGN_PAY,
                payload={"form": "FCC 499-A"},
                decision=DECISION_PENDING,
            )
        )

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


async def test_stuck_task_with_no_pending_approval_is_self_healed(http):
    """Regression for the bug Amber Sidney Hunt hit: an old Task.status=
    'awaiting_approval' row with its approval already decided would
    show as 1 in Live Activity even though the Approval queue showed 0
    pending. /activity now sources awaiting from PENDING APPROVALS, and
    self-heals the stuck Task.status on every poll."""
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "stuck@a.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Amber"})).json()["id"]
    )
    async with maker() as s:
        p = Project(client_id=cid)
        s.add(p)
        await s.flush()
        # A task that someone forgot to close out: status awaiting_approval
        # but the only Approval attached is already approved.
        stuck = Task(
            project_id=p.id,
            agent="carrier",
            status="awaiting_approval",
            input={"instruction": "NECA-OCN-2"},
        )
        s.add(stuck)
        await s.flush()
        s.add(
            Approval(
                task_id=stuck.id,
                action_type="queue_filing_submission",
                tier=TIER_SIGN_PAY,
                payload={"form": "NECA-OCN-2"},
                decision="approved",  # already decided — task should not be in-flight
            )
        )
        stuck_id = stuck.id
        await s.commit()

    r = await c.get("/activity", headers=h)
    assert r.status_code == 200
    body = r.json()
    # No pending approval → nothing to wait on.
    assert body["awaiting_approval"] == []
    assert body["totals"]["awaiting_approval"] == 0
    # Self-heal: the stuck task is moved to completed.
    async with maker() as s:
        t = await s.get(Task, stuck_id)
        assert t.status == "completed"


async def test_awaiting_lists_one_row_per_task_even_with_multi_approvals(http):
    """A single task can queue multiple approvals (PM delegation, batch
    drafts). The awaiting list should show one entry per task, not one
    per approval — operators care about tasks they need to clear."""
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "multi@a.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    )
    async with maker() as s:
        p = Project(client_id=cid)
        s.add(p)
        await s.flush()
        t = Task(
            project_id=p.id,
            agent="compliance",
            status="awaiting_approval",
            input={"instruction": "Draft a + b"},
        )
        s.add(t)
        await s.flush()
        s.add_all(
            [
                Approval(
                    task_id=t.id,
                    action_type="queue_filing_submission",
                    tier=TIER_SIGN_PAY,
                    payload={"form": "A"},
                    decision=DECISION_PENDING,
                ),
                Approval(
                    task_id=t.id,
                    action_type="queue_filing_submission",
                    tier=TIER_SIGN_PAY,
                    payload={"form": "B"},
                    decision=DECISION_PENDING,
                ),
            ]
        )
        await s.commit()

    r = await c.get("/activity", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert len(body["awaiting_approval"]) == 1
    assert body["awaiting_approval"][0]["agent"] == "compliance"


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
