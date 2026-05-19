"""Audit CSV export. Per-client + operator-wide; both are owner-scoped.
The operator-wide export never leaks rows from another operator's
clients. Append-only invariant: rows in CSV match rows in DB exactly.
"""

import csv
import io
import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.audit import record_audit
from app.db import Base, get_db
from app.main import app


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

    async def _db():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, maker
    app.dependency_overrides.clear()
    await engine.dispose()


async def _register(c: AsyncClient, email: str) -> dict:
    r = await c.post(
        "/auth/register",
        json={"email": email, "password": "supersecret", "name": "Op"},
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _seed_audit(maker, client_id, *, actor: str, action: str, **fields):
    async with maker() as s:
        await record_audit(s, actor=actor, action=action, client_id=client_id, **fields)
        await s.commit()


async def test_per_client_audit_csv_export(ctx):
    c, maker = ctx
    h = await _register(c, "a@example.com")
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Acme VoIP"})).json()["id"]
    )

    # Seed three entries.
    await _seed_audit(
        maker, cid, actor="op@example.com", action="approval.approved",
        subject="approval:1", after={"decision": "approved"},
    )
    await _seed_audit(
        maker, cid, actor="system", action="approval.executed",
        subject="approval:1", after={"result": "OK"},
    )
    await _seed_audit(
        maker, cid, actor="op@example.com", action="approval.rejected",
        subject="approval:2", after={"decision": "rejected", "note": "policy"},
    )

    r = await c.get(f"/clients/{cid}/audit.csv", headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "Acme-VoIP-audit.csv" in r.headers["content-disposition"]

    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == [
        "ts", "actor", "action", "subject", "client_id", "before", "after"
    ]
    # Three audit rows + one header.
    assert len(rows) == 4
    actions = {row[2] for row in rows[1:]}
    assert actions == {"approval.approved", "approval.executed", "approval.rejected"}
    # JSON serialization preserves the structured details.
    rejected = next(row for row in rows[1:] if row[2] == "approval.rejected")
    assert '"note":"policy"' in rejected[6]


async def test_operator_wide_csv_is_owner_scoped(ctx):
    c, maker = ctx
    ha = await _register(c, "a@example.com")
    hb = await _register(c, "b@example.com")
    ca = uuid.UUID(
        (await c.post("/clients", headers=ha, json={"name": "Owner-A"})).json()["id"]
    )
    cb = uuid.UUID(
        (await c.post("/clients", headers=hb, json={"name": "Owner-B"})).json()["id"]
    )
    await _seed_audit(maker, ca, actor="a@example.com", action="a.event", subject="x")
    await _seed_audit(maker, cb, actor="b@example.com", action="b.event", subject="y")

    # Operator A's export contains only their row.
    r = await c.get("/audit.csv", headers=ha)
    assert r.status_code == 200
    rows = list(csv.reader(io.StringIO(r.text)))
    actions = {row[2] for row in rows[1:]}
    assert actions == {"a.event"}
    # B is never even mentioned.
    assert "b.event" not in r.text
    assert str(cb) not in r.text


async def test_per_client_csv_404_for_other_operator(ctx):
    c, _ = ctx
    ha = await _register(c, "a@example.com")
    hb = await _register(c, "b@example.com")
    ca = uuid.UUID(
        (await c.post("/clients", headers=ha, json={"name": "A"})).json()["id"]
    )
    r = await c.get(f"/clients/{ca}/audit.csv", headers=hb)
    assert r.status_code == 404
