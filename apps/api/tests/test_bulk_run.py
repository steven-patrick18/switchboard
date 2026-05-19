"""Bounded bulk sweep of a client's queued sub-tasks. Runnable ones run
(their T3 actions still queue for approval); pm/no-objective are skipped
with reasons; a failing task is isolated and marked failed without
aborting the sweep; the cap bounds spend. Fake Anthropic, no API spend.
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
        input_tokens=10,
        output_tokens=5,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )


class _Fake:
    """Stateless per-run: first model call (messages==1) → T3 tool_use;
    follow-up → end_turn. Raises if the instruction says BOOM."""

    def __init__(self):
        self.messages = self

    async def create(self, **kw):
        msgs = kw.get("messages") or []
        if len(msgs) == 1:
            if "BOOM" in str(msgs[0].get("content", "")):
                raise RuntimeError("simulated agent failure")
            return SimpleNamespace(
                stop_reason="tool_use",
                usage=_usage(),
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        id="t1",
                        name="queue_filing_submission",
                        input={"form": "FCC 499-A", "summary": "x"},
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
    app.dependency_overrides[get_anthropic_client] = lambda: _Fake()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c, maker
    app.dependency_overrides.clear()
    await engine.dispose()


async def _client(c) -> tuple[dict, uuid.UUID]:
    reg = await c.post(
        "/auth/register",
        json={"email": f"op{uuid.uuid4().hex[:6]}@acme.com",
              "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    )
    return h, cid


async def _seed(maker, client_id, specs: list[tuple[str, dict]]) -> None:
    async with maker() as s:
        p = Project(client_id=client_id)
        s.add(p)
        await s.flush()
        for agent, inp in specs:
            s.add(Task(project_id=p.id, agent=agent, status="queued", input=inp))
        await s.commit()


async def test_bulk_sweep_runs_skips_and_isolates_failures(http):
    c, maker = http
    h, cid = await _client(c)
    await _seed(
        maker,
        cid,
        [
            ("compliance", {"objective": "File 499 #1"}),
            ("compliance", {"objective": "File 499 #2"}),
            ("pm", {"objective": "orchestrate"}),
            ("compliance", {}),  # no objective
            ("compliance", {"objective": "BOOM please"}),
        ],
    )

    r = await c.post(f"/clients/{cid}/tasks/run-queued", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()

    assert len(body["ran"]) == 2
    assert all(len(x["approval_ids"]) == 1 for x in body["ran"])
    assert {s["reason"] for s in body["skipped"]} == {
        "agent 'pm' is not runnable",
        "task has no objective",
    }
    assert len(body["failed"]) == 1 and "error" in body["failed"][0]["reason"]
    assert body["capped"] is False

    tasks = (await c.get(f"/clients/{cid}/tasks", headers=h)).json()
    by_status: dict[str, int] = {}
    for t in tasks:
        by_status[t["status"]] = by_status.get(t["status"], 0) + 1
    assert by_status["awaiting_approval"] == 2
    assert by_status["failed"] == 1
    assert by_status["queued"] == 2  # pm + no-objective untouched

    # Re-sweep: nothing new runs (the two ran are no longer queued).
    r2 = await c.post(f"/clients/{cid}/tasks/run-queued", headers=h)
    assert r2.json()["ran"] == []


async def test_bulk_cap_and_isolation(http):
    c, maker = http
    h, cid = await _client(c)
    await _seed(
        maker,
        cid,
        [("compliance", {"objective": f"File {i}"}) for i in range(3)],
    )

    r = await c.post(f"/clients/{cid}/tasks/run-queued?limit=1", headers=h)
    body = r.json()
    assert len(body["ran"]) == 1
    assert body["capped"] is True
    assert sum(1 for s in body["skipped"] if "cap reached" in s["reason"]) == 2

    # Foreign operator cannot sweep this client.
    h2, _ = await _client(c)
    assert (
        await c.post(f"/clients/{cid}/tasks/run-queued", headers=h2)
    ).status_code == 404
