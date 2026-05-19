"""The approval gateway is the safety boundary. This verifies that a T3
tool call is queued (Approval row, decision=pending) and NOT executed,
while a T0 tool runs inline — using a fake Anthropic client (no API spend).
"""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.agents.base import run_agent
from app.agents.compliance import COMPLIANCE_AGENT
from app.db import Base
from app.models.agent_run import AgentRun
from app.models.approval import DECISION_PENDING, TIER_SIGN_PAY, Approval

# Import models so metadata is complete for create_all.
import app.models  # noqa: F401


def _usage():
    return SimpleNamespace(
        input_tokens=120,
        output_tokens=80,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )


class FakeMessages:
    def __init__(self):
        self._calls = 0

    async def create(self, **_kwargs):
        self._calls += 1
        if self._calls == 1:
            # Turn 1: agent calls a safe T0 tool and a gated T3 tool.
            return SimpleNamespace(
                stop_reason="tool_use",
                usage=_usage(),
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        id="tu_lookup",
                        name="lookup_fcc_requirement",
                        input={"topic": "499"},
                    ),
                    SimpleNamespace(
                        type="tool_use",
                        id="tu_submit",
                        name="queue_filing_submission",
                        input={"form": "FCC 499-A", "summary": "Annual 499 for Acme"},
                    ),
                ],
            )
        # Turn 2: agent wraps up.
        return SimpleNamespace(
            stop_reason="end_turn",
            usage=_usage(),
            content=[SimpleNamespace(type="text", text="Prepared and queued.")],
        )


class FakeAnthropic:
    def __init__(self):
        self.messages = FakeMessages()


@pytest.fixture
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


async def test_t3_is_queued_not_executed_and_t0_runs(db: AsyncSession):
    task_id = uuid.uuid4()

    result = await run_agent(
        COMPLIANCE_AGENT,
        client=FakeAnthropic(),
        db=db,
        task_id=task_id,
        instruction="File the annual 499 for Acme VoIP.",
    )

    assert result.text == "Prepared and queued."
    # Exactly one approval — the T3 submission. The T0 lookup ran inline and
    # produced no approval.
    assert len(result.approval_ids) == 1

    approvals = (await db.scalars(select(Approval))).all()
    assert len(approvals) == 1
    appr = approvals[0]
    assert appr.action_type == "queue_filing_submission"
    assert appr.tier == TIER_SIGN_PAY
    assert appr.decision == DECISION_PENDING
    assert appr.payload == {"form": "FCC 499-A", "summary": "Annual 499 for Acme"}
    assert appr.task_id == task_id

    # AgentRun accounting persisted across both turns.
    run = (await db.scalars(select(AgentRun))).one()
    assert run.agent == "compliance"
    assert run.tokens_in == 240
    assert run.tokens_out == 160
    assert float(run.cost) > 0
