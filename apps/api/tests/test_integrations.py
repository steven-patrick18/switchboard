"""Portal-integration adapter: wired action decrypts via the vault
(audited as `credential.accessed`), invokes the demo backend, and
folds the structured result into the execution_result text. Unwired
actions retain the prior "not yet wired" behavior.
"""

import uuid

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.execution import execute_approval
from app.integrations import get_handler
from app.models import Approval, AuditLog, Document
from app.models.approval import DECISION_APPROVED, TIER_APPROVE
from app.vault import store_credential


async def test_demo_handler_returns_structured_result():
    handler = get_handler("fcc_cores", "check_filer_status")
    assert handler is not None
    out = await handler("any-secret", {"filer_id": "832123"})
    assert out.backend == "demo"
    assert out.status == "ACTIVE"
    assert out.detail["filer_id"] == "832123"
    assert get_handler("nope", "x") is None


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


async def test_executor_runs_wired_integration_and_audits(db: AsyncSession):
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
            "action": "check_filer_status",
            "summary": "look up filer",
            "params": {"filer_id": "832123"},
        },
        decision=DECISION_APPROVED,
    )
    db.add(a)
    await db.flush()
    result = await execute_approval(a, cid, db)
    await db.commit()

    # Adapter ran and its structured result was folded into the text.
    assert "Backend: demo" in result
    assert "Status: ACTIVE" in result
    assert "832123" in result

    # Document Hub artifact still materialized.
    docs = (
        await db.scalars(select(Document).where(Document.client_id == cid))
    ).all()
    assert any(
        d.type == "portal_action:fcc_cores:check_filer_status" for d in docs
    )

    # The vault was decrypted — `credential.accessed` audit entry exists.
    actions = {
        (x.action, x.actor) for x in (await db.scalars(select(AuditLog))).all()
    }
    assert ("credential.accessed", "system") in actions
