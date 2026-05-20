"""Task.status close-out + send-back-to-agent flow.

The bug being fixed here: when an operator approved or rejected the
LAST pending approval on a task, the task itself stayed at
`awaiting_approval` forever — so the Live Activity feed kept showing
it as in-flight even though everything was done. The fix is in
`_close_task_if_done` (apps/api/app/api/routes/approvals.py).
"""

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Approval, Project, Task
from app.models.approval import DECISION_PENDING, TIER_APPROVE, TIER_SIGN_PAY


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
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c, maker
    app.dependency_overrides.clear()
    await engine.dispose()


async def _seed_two_approvals(maker, client_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Returns (task_id, approval_a, approval_b) — both pending on the
    same task. Lets us test that the task closes only after BOTH are
    decided, not after the first."""
    async with maker() as s:
        p = Project(client_id=client_id)
        s.add(p)
        await s.flush()
        t = Task(project_id=p.id, agent="carrier", status="awaiting_approval", input={})
        s.add(t)
        await s.flush()
        a = Approval(
            task_id=t.id,
            action_type="queue_filing_submission",
            tier=TIER_SIGN_PAY,
            payload={"form": "NECA-OCN-2"},
            decision=DECISION_PENDING,
        )
        b = Approval(
            task_id=t.id,
            action_type="queue_filing_submission",
            tier=TIER_SIGN_PAY,
            payload={"form": "FCC 499-A"},
            decision=DECISION_PENDING,
        )
        s.add_all([a, b])
        await s.flush()
        out = (t.id, a.id, b.id)
        await s.commit()
        return out


async def test_task_closes_only_after_every_approval_decided(ctx):
    c, maker = ctx
    reg = await c.post(
        "/auth/register",
        json={"email": "op@a.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    )
    task_id, a1, a2 = await _seed_two_approvals(maker, cid)

    # Approve the first — task must remain awaiting_approval because
    # a2 is still pending.
    r = await c.post(f"/approvals/{a1}/approve", headers=h, json={})
    assert r.status_code == 200
    async with maker() as s:
        t = await s.get(Task, task_id)
        assert t.status == "awaiting_approval"

    # Reject the second — now the task is fully decided, must close.
    r = await c.post(
        f"/approvals/{a2}/reject", headers=h, json={"reason": "not needed yet"}
    )
    assert r.status_code == 200
    async with maker() as s:
        t = await s.get(Task, task_id)
        assert t.status == "completed"


async def test_send_back_rejects_and_creates_queued_task(ctx):
    """No Anthropic key configured → the new task lands as 'queued'
    (operator can hit Start later) and the rejected approval closes
    its parent task."""
    c, maker = ctx
    reg = await c.post(
        "/auth/register",
        json={"email": "op2@a.com", "password": "supersecret", "name": "Op2"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Acme2"})).json()["id"]
    )

    # Single-approval task — much like the Amano OCN flow.
    async with maker() as s:
        p = Project(client_id=cid)
        s.add(p)
        await s.flush()
        t = Task(
            project_id=p.id,
            agent="carrier",
            status="awaiting_approval",
            input={"instruction": "Draft the NECA-OCN-2 + LOA."},
        )
        s.add(t)
        await s.flush()
        appr = Approval(
            task_id=t.id,
            action_type="queue_filing_submission",
            tier=TIER_SIGN_PAY,
            payload={"form": "NECA-OCN-2", "fields": {"frn": "TBD"}},
            decision=DECISION_PENDING,
        )
        s.add(appr)
        await s.flush()
        task_id, aid = t.id, appr.id
        await s.commit()

    r = await c.post(
        f"/approvals/{aid}/send-back",
        headers=h,
        json={"feedback": "FRN is 0001234567 — fill it in and requeue."},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["rejected_approval_id"] == str(aid)
    # No ANTHROPIC_API_KEY set in this test workspace → ran=False.
    assert body["ran"] is False
    assert body["new_approval_ids"] == []
    new_task_id = uuid.UUID(body["new_task_id"])

    async with maker() as s:
        # Original task closed.
        t = await s.get(Task, task_id)
        assert t.status == "completed"
        # Original approval recorded as rejected with the feedback.
        a = await s.get(Approval, aid)
        assert a.decision == "rejected"
        assert "0001234567" in (a.note or "")
        # New task is queued for the same agent.
        nt = await s.get(Task, new_task_id)
        assert nt.status == "queued"
        assert nt.agent == "carrier"
        assert "OPERATOR FEEDBACK" in (nt.input or {}).get("instruction", "")
        assert "0001234567" in (nt.input or {}).get("instruction", "")


async def test_send_back_blank_feedback_400(ctx):
    """The feedback IS the operator's correction — blank is rejected so
    the audit trail always explains the redo."""
    c, maker = ctx
    reg = await c.post(
        "/auth/register",
        json={"email": "op3@a.com", "password": "supersecret", "name": "Op3"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Acme3"})).json()["id"]
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
            action_type="request_portal_action",
            tier=TIER_APPROVE,
            payload={"service": "fcc_cores", "action": "check_filer_status"},
            decision=DECISION_PENDING,
        )
        s.add(appr)
        await s.flush()
        aid = appr.id
        await s.commit()

    r = await c.post(
        f"/approvals/{aid}/send-back", headers=h, json={"feedback": "   "}
    )
    assert r.status_code == 422
