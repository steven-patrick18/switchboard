"""Per-client autonomy mode: in 'autonomous' mode T2 actions auto-execute
(an Approval row is still recorded with decision='approved' so the
forensic trail is complete), while T3 ALWAYS queues regardless — that's
the legal floor for filings under penalty of perjury and e-signatures.

In-memory DB, fake Anthropic client, no API spend.
"""

import uuid
from types import SimpleNamespace

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.agents.base import AgentSpec, run_agent
from app.agents.tools import (
    queue_filing_submission,
    request_portal_action,
)
from app.audit import record_audit  # noqa: F401  -- exercises the audit path
from app.db import Base
from app.models import Approval, AuditLog, Client, Project, Task
from app.models.approval import (
    DECISION_APPROVED,
    DECISION_PENDING,
    TIER_APPROVE,
    TIER_SIGN_PAY,
)
from app.models.client import AUTONOMY_AUTONOMOUS, AUTONOMY_SUPERVISED

import app.models  # noqa: F401


def _usage():
    return SimpleNamespace(
        input_tokens=100,
        output_tokens=60,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )


class _Fake:
    """Turn 1 returns the given tool blocks; turn 2 stops the loop."""

    def __init__(self, tool_blocks):
        self._tool_blocks = tool_blocks
        self.messages = self
        self._calls = 0

    async def create(self, **_kw):
        self._calls += 1
        if self._calls == 1:
            return SimpleNamespace(
                stop_reason="tool_use", usage=_usage(), content=self._tool_blocks
            )
        return SimpleNamespace(
            stop_reason="end_turn",
            usage=_usage(),
            content=[SimpleNamespace(type="text", text="done")],
        )


def _tu(tool_id, name, inp):
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=inp)


def _agent_with(tools):
    return AgentSpec(
        name="testagent",
        system_prompt="test",
        tools=list(tools),
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


async def _setup_workspace(db: AsyncSession, autonomy: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Seed: owner user, owned client (with desired autonomy), project,
    task. Returns (client_id, project_id, task_id)."""
    from app.models.user import User  # noqa: PLC0415

    u = User(email="o@a.com", hashed_password="x", name="Op")
    db.add(u)
    await db.flush()
    c = Client(owner_id=u.id, name="Acme", autonomy_level=autonomy)
    db.add(c)
    await db.flush()
    p = Project(client_id=c.id)
    db.add(p)
    await db.flush()
    t = Task(project_id=p.id, agent="testagent", status="running", input={})
    db.add(t)
    await db.flush()
    return c.id, p.id, t.id


async def test_supervised_default_queues_t2_pending(db: AsyncSession):
    """Sanity baseline: in the default (supervised) mode, a T2 tool call
    creates a pending Approval row and does NOT execute."""
    client_id, project_id, task_id = await _setup_workspace(db, AUTONOMY_SUPERVISED)
    spec = _agent_with([request_portal_action])
    fake = _Fake(
        [
            _tu(
                "u1",
                "request_portal_action",
                {
                    "service": "fcc_cores",
                    "action": "check_filer_status",
                    "summary": "Look up CORES filer status",
                },
            ),
        ]
    )

    result = await run_agent(
        spec,
        client=fake,
        db=db,
        task_id=task_id,
        instruction="Check CORES",
        client_id=client_id,
        project_id=project_id,
    )

    assert len(result.approval_ids) == 1
    appr = (await db.scalars(select(Approval))).one()
    assert appr.tier == TIER_APPROVE
    assert appr.decision == DECISION_PENDING
    assert appr.executed_at is None


async def test_autonomous_mode_executes_t2_without_queueing(db: AsyncSession):
    """In autonomous mode, a T2 tool call should execute immediately,
    record an Approval with decision='approved', and NOT add to
    `approval_ids` (since nothing is waiting on the operator)."""
    client_id, project_id, task_id = await _setup_workspace(db, AUTONOMY_AUTONOMOUS)
    # Seed a credential so request_portal_action's executor doesn't bail.
    from app.vault import store_credential  # noqa: PLC0415

    await store_credential(
        db,
        client_id=client_id,
        service="fcc_cores",
        username="user",
        secret="pw",
    )

    spec = _agent_with([request_portal_action])
    fake = _Fake(
        [
            _tu(
                "u1",
                "request_portal_action",
                {
                    "service": "fcc_cores",
                    "action": "check_filer_status",
                    "summary": "Look up CORES filer status",
                },
            ),
        ]
    )

    result = await run_agent(
        spec,
        client=fake,
        db=db,
        task_id=task_id,
        instruction="Check CORES",
        client_id=client_id,
        project_id=project_id,
        autonomy_level=AUTONOMY_AUTONOMOUS,
    )

    # No approvals are pending for the operator — the agent self-served.
    assert result.approval_ids == []
    appr = (await db.scalars(select(Approval))).one()
    assert appr.tier == TIER_APPROVE
    assert appr.decision == DECISION_APPROVED
    assert appr.executed_at is not None
    assert appr.execution_result is not None
    assert "autonomous" in (appr.note or "").lower()

    # Audit logs the action under the autonomous-marked actor.
    audit_actions = [
        row.action
        for row in (await db.scalars(select(AuditLog))).all()
    ]
    assert "approval.auto_executed" in audit_actions


async def test_autonomous_mode_still_queues_t3_filings(db: AsyncSession):
    """The legal floor: T3 (filings under penalty of perjury, e-signatures)
    ALWAYS queues — autonomous mode cannot bypass it."""
    client_id, project_id, task_id = await _setup_workspace(db, AUTONOMY_AUTONOMOUS)
    spec = _agent_with([queue_filing_submission])
    fake = _Fake(
        [
            _tu(
                "u1",
                "queue_filing_submission",
                {
                    "form": "NECA-OCN-2",
                    "summary": "OCN application package",
                    "payload": {"filer": "Acme"},
                },
            ),
        ]
    )

    result = await run_agent(
        spec,
        client=fake,
        db=db,
        task_id=task_id,
        instruction="File OCN",
        client_id=client_id,
        project_id=project_id,
        autonomy_level=AUTONOMY_AUTONOMOUS,
    )

    # T3 still produces a pending approval the operator must decide on.
    assert len(result.approval_ids) == 1
    appr = (await db.scalars(select(Approval))).one()
    assert appr.tier == TIER_SIGN_PAY
    assert appr.decision == DECISION_PENDING
    assert appr.executed_at is None
