"""PM real orchestration: it reads intake status and decomposes a launch
into queued sub-tasks for compliance/document — without taking any
external action (no approvals). Plus the tasks-observability endpoint.
Fake client, no API spend.
"""

import uuid
from types import SimpleNamespace

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.agents.base import run_agent
from app.agents.pm import PM_AGENT
from app.db import Base, get_db
from app.main import app  # registers model metadata
from app.models import Project, Task
from app.models.agent_run import AgentRun
from app.models.approval import Approval


def _usage():
    return SimpleNamespace(
        input_tokens=90,
        output_tokens=40,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )


def _tu(tid, name, inp):
    return SimpleNamespace(type="tool_use", id=tid, name=name, input=inp)


class _FakePM:
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
                    _tu("a", "check_intake_status", {}),
                    _tu("b", "assign_task", {"agent": "compliance", "objective": "File FCC 499"}),
                    _tu("c", "assign_task", {"agent": "document", "objective": "Draft ToS"}),
                    _tu("d", "assign_task", {"agent": "pm", "objective": "loop"}),
                ],
            )
        return SimpleNamespace(
            stop_reason="end_turn",
            usage=_usage(),
            content=[SimpleNamespace(type="text", text="Plan ready.")],
        )


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
    async with maker() as session:
        yield session
    await engine.dispose()


async def test_pm_assigns_subtasks_no_approval(db: AsyncSession):
    project_id = uuid.uuid4()
    result = await run_agent(
        PM_AGENT,
        client=_FakePM(),
        db=db,
        task_id=uuid.uuid4(),
        instruction="Plan the Acme VoIP launch.",
        client_id=uuid.uuid4(),
        project_id=project_id,
    )

    assert result.text == "Plan ready."
    # PM takes no external action — nothing is gated.
    assert result.approval_ids == []
    assert (await db.scalars(select(Approval))).all() == []

    # Two valid delegations created queued tasks; the invalid 'pm' target
    # created none (validated, returned an error to the model).
    tasks = (await db.scalars(select(Task))).all()
    assert {(t.agent, t.input["objective"]) for t in tasks} == {
        ("compliance", "File FCC 499"),
        ("document", "Draft ToS"),
    }
    assert all(t.input["assigned_by"] == "pm" and t.status == "queued" for t in tasks)
    assert all(t.project_id == project_id for t in tasks)

    run = (await db.scalars(select(AgentRun))).one()
    assert run.agent == "pm"


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


async def test_tasks_endpoint_scoped(http):
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op@acme.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    )

    async with maker() as s:
        proj = Project(client_id=cid)
        s.add(proj)
        await s.flush()
        s.add(Task(project_id=proj.id, agent="compliance", status="queued",
                   input={"objective": "File 499"}))
        await s.commit()

    r = await c.get(f"/clients/{cid}/tasks", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1 and body[0]["agent"] == "compliance"

    reg2 = await c.post(
        "/auth/register",
        json={"email": "b@acme.com", "password": "supersecret", "name": "B"},
    )
    hb = {"Authorization": f"Bearer {reg2.json()['access_token']}"}
    assert (await c.get(f"/clients/{cid}/tasks", headers=hb)).status_code == 404
