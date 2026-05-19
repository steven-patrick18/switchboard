"""Chained execution: a queued sub-task (as PM would create) runs through
its agent via the runtime + approval gateway. The sub-agent's T3 action
still queues for approval. Fake Anthropic via dependency override — no
API spend.
"""

import uuid
from types import SimpleNamespace

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.llm import get_anthropic_client
from app.main import app
from app.models import Project, Task


def _usage():
    return SimpleNamespace(
        input_tokens=80,
        output_tokens=40,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )


class _FakeCompliance:
    def __init__(self):
        self.messages = self
        self._n = 0

    async def create(self, **_kw):
        self._n += 1
        if self._n == 1:
            return SimpleNamespace(
                stop_reason="tool_use",
                usage=_usage(),
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        id="t1",
                        name="queue_filing_submission",
                        input={"form": "FCC 499-A", "summary": "annual"},
                    )
                ],
            )
        return SimpleNamespace(
            stop_reason="end_turn",
            usage=_usage(),
            content=[SimpleNamespace(type="text", text="prepared")],
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

    async def _override_db():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_anthropic_client] = lambda: _FakeCompliance()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c, maker
    app.dependency_overrides.clear()
    await engine.dispose()


async def _seed_task(maker, client_id, agent, status="queued") -> uuid.UUID:
    async with maker() as s:
        p = Project(client_id=client_id)
        s.add(p)
        await s.flush()
        t = Task(
            project_id=p.id,
            agent=agent,
            status=status,
            input={"objective": "File the annual FCC 499 for Acme"},
        )
        s.add(t)
        await s.flush()
        tid = t.id
        await s.commit()
        return tid


async def test_run_queued_subtask_chains_through_gateway(http):
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op@acme.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    )

    tid = await _seed_task(maker, cid, "compliance")

    r = await c.post(f"/clients/{cid}/tasks/{tid}/run", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent"] == "compliance"
    assert body["text"] == "prepared"
    # The sub-agent's T3 filing landed in the approval queue, not executed.
    assert len(body["approval_ids"]) == 1

    tasks = (await c.get(f"/clients/{cid}/tasks", headers=h)).json()
    run_task = next(t for t in tasks if t["id"] == str(tid))
    assert run_task["status"] == "awaiting_approval"
    assert (await c.get("/approvals", headers=h)).json()[0]["action_type"] == (
        "queue_filing_submission"
    )

    # Re-running a non-queued task is blocked.
    r = await c.post(f"/clients/{cid}/tasks/{tid}/run", headers=h)
    assert r.status_code == 409


async def test_run_guards(http):
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op@acme.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    )

    # PM tasks are not runnable here (no recursive PM).
    pm_tid = await _seed_task(maker, cid, "pm")
    assert (await c.post(f"/clients/{cid}/tasks/{pm_tid}/run", headers=h)).status_code == 400

    # Foreign operator cannot run another's task.
    ok_tid = await _seed_task(maker, cid, "compliance")
    reg2 = await c.post(
        "/auth/register",
        json={"email": "b@acme.com", "password": "supersecret", "name": "B"},
    )
    hb = {"Authorization": f"Bearer {reg2.json()['access_token']}"}
    assert (
        await c.post(f"/clients/{cid}/tasks/{ok_tid}/run", headers=hb)
    ).status_code == 404
