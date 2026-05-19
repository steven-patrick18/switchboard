"""Immutable audit trail: queuing (agent), approve/edit, execute, and
reject each append an entry with the right actor and before/after. The
read endpoint is operator-scoped. Fake Anthropic, no API spend.
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


def _usage():
    return SimpleNamespace(
        input_tokens=10,
        output_tokens=5,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )


class _Fake:
    def __init__(self):
        self.messages = self

    async def create(self, **kw):
        if len(kw.get("messages") or []) == 1:
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

    async def _odb():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = _odb
    app.dependency_overrides[get_anthropic_client] = lambda: _Fake()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


async def test_audit_trail(http):
    c = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op@acme.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]

    # Agent queues a T3 action → audited as approval.queued by the agent.
    r = await c.post(
        f"/agents/compliance/run",
        headers=h,
        json={"client_id": cid, "instruction": "File the 499"},
    )
    assert r.status_code == 200, r.text
    a1 = r.json()["approval_ids"][0]

    audit = (await c.get(f"/clients/{cid}/audit", headers=h)).json()
    queued = [e for e in audit if e["action"] == "approval.queued"]
    assert len(queued) == 1
    assert queued[0]["actor"] == "compliance"
    assert queued[0]["subject"] == f"approval:{a1}"
    assert queued[0]["after"]["payload"]["form"] == "FCC 499-A"

    # Edit & approve → approval.edited (before/after) + approval.executed.
    r = await c.post(
        f"/approvals/{a1}/approve",
        headers=h,
        json={"payload_override": {"form": "FCC 499-A", "fixed": True}},
    )
    assert r.status_code == 200
    audit = (await c.get(f"/clients/{cid}/audit", headers=h)).json()
    actions = [e["action"] for e in audit]
    assert "approval.edited" in actions
    assert "approval.executed" in actions
    edited = next(e for e in audit if e["action"] == "approval.edited")
    assert edited["actor"] == "op@acme.com"
    assert edited["before"]["payload"]["form"] == "FCC 499-A"
    assert edited["after"]["payload"]["fixed"] is True
    executed = next(e for e in audit if e["action"] == "approval.executed")
    assert executed["actor"] == "system"
    assert "Document Hub" in executed["after"]["result"]

    # A second queued action, then reject → approval.rejected.
    r = await c.post(
        f"/agents/compliance/run",
        headers=h,
        json={"client_id": cid, "instruction": "File another"},
    )
    a2 = r.json()["approval_ids"][0]
    await c.post(f"/approvals/{a2}/reject", headers=h, json={"reason": "wrong tone"})
    audit = (await c.get(f"/clients/{cid}/audit", headers=h)).json()
    rej = next(e for e in audit if e["action"] == "approval.rejected")
    assert rej["actor"] == "op@acme.com"
    assert rej["after"]["note"] == "wrong tone"

    # Newest-first ordering.
    ts = [e["ts"] for e in audit]
    assert ts == sorted(ts, reverse=True)

    # Operator-scoped.
    reg2 = await c.post(
        "/auth/register",
        json={"email": "b@acme.com", "password": "supersecret", "name": "B"},
    )
    hb = {"Authorization": f"Bearer {reg2.json()['access_token']}"}
    assert (await c.get(f"/clients/{cid}/audit", headers=hb)).status_code == 404
