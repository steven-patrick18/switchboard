"""Per-service action catalog: the agent picks from a known menu, the
executor validates required params, and the approval UI gets a nicer
label. No API spend.
"""

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.execution import execute_approval
from app.main import app
from app.models import Approval, Document
from app.models.approval import DECISION_APPROVED, TIER_APPROVE
from app.portal_actions import (
    ACTIONS,
    get_action,
    list_actions,
    missing_params,
    services,
)
from app.vault import store_credential


def test_registry_has_seed_entries_and_distinct_services():
    entries = list_actions()
    assert len(entries) >= 6
    keys = {(e.service, e.action) for e in entries}
    assert ("fcc_cores", "submit_499_q") in keys
    assert ("irs", "check_ein_status") in keys
    assert ("state_puc_tx", "check_cpcn_status") in keys
    # Service list is distinct.
    s = services()
    assert len(s) == len(set(s))


def test_get_action_and_missing_params():
    spec = get_action("fcc_cores", "submit_499_q")
    assert spec is not None and spec.tier == "T3"
    assert missing_params(spec, None) == ["quarter", "revenue"]
    assert missing_params(spec, {"quarter": "Q1"}) == ["revenue"]
    assert missing_params(spec, {"quarter": "Q1", "revenue": 1000}) == []
    assert get_action("nope", "nope") is None


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
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_executor_blocks_when_required_params_missing(db: AsyncSession):
    cid = uuid.uuid4()
    await store_credential(
        db, client_id=cid, service="fcc_cores", secret="TOPSECRET"
    )
    a = Approval(
        task_id=uuid.uuid4(),
        action_type="request_portal_action",
        tier=TIER_APPROVE,
        payload={
            "service": "fcc_cores",
            "action": "submit_499_q",
            "summary": "go",
            "params": {},  # missing quarter + revenue
        },
        decision=DECISION_APPROVED,
    )
    db.add(a)
    await db.flush()
    result = await execute_approval(a, cid, db)
    assert "missing required params" in result.lower()
    assert "quarter" in result and "revenue" in result
    # No Document materialized for an invalid request.
    assert (
        await db.scalars(select(Document).where(Document.client_id == cid))
    ).all() == []


async def test_executor_allows_free_form_action(db: AsyncSession):
    cid = uuid.uuid4()
    await store_credential(
        db, client_id=cid, service="fcc_cores", secret="TOPSECRET"
    )
    a = Approval(
        task_id=uuid.uuid4(),
        action_type="request_portal_action",
        tier=TIER_APPROVE,
        payload={
            "service": "fcc_cores",
            "action": "novel_one_off_action",
            "summary": "x",
        },
        decision=DECISION_APPROVED,
    )
    db.add(a)
    await db.flush()
    result = await execute_approval(a, cid, db)
    assert "not in registry" in result
    docs = (
        await db.scalars(select(Document).where(Document.client_id == cid))
    ).all()
    assert any(
        d.type == "portal_action:fcc_cores:novel_one_off_action" for d in docs
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
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


async def test_catalog_endpoint(http):
    c = http
    # Auth required.
    assert (await c.get("/portal-actions")).status_code in (401, 403)

    reg = await c.post(
        "/auth/register",
        json={"email": "p@acme.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    r = await c.get("/portal-actions", headers=h)
    assert r.status_code == 200
    items = r.json()
    assert len(items) == len(ACTIONS)
    by_key = {(i["service"], i["action"]): i for i in items}
    spec = by_key[("fcc_cores", "submit_499_q")]
    assert spec["label"].startswith("FCC CORES")
    assert spec["tier"] == "T3"
    assert "quarter" in spec["required_params"]
