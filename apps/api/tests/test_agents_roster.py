"""Phase 1 roster: PM + Compliance + Document. Verifies the registry and
that the same approval-gateway invariant holds for the new agents — T0
tools run inline, T3 tools queue and never execute. Fake client, no spend.
"""

import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.agents.base import run_agent
from app.agents.carrier import CARRIER_AGENT
from app.agents.document import DOCUMENT_AGENT
from app.agents.pm import PM_AGENT
from app.agents.registry import AGENTS, get_agent
from app.db import Base
from app.models.agent_run import AgentRun
from app.models.approval import DECISION_PENDING, TIER_SIGN_PAY, Approval

import app.models  # noqa: F401


def _usage():
    return SimpleNamespace(
        input_tokens=100,
        output_tokens=60,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )


class _Fake:
    """Turn 1 calls the given tool blocks; turn 2 ends the turn."""

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


def test_registry_resolves_seven_agent_roster():
    assert set(AGENTS) == {
        "pm",
        "readiness",
        "intake",
        "compliance",
        "state_licensing",
        "carrier",
        "document",
    }
    assert get_agent("pm") is PM_AGENT
    assert get_agent("document") is DOCUMENT_AGENT
    assert get_agent("nope") is None


async def test_pm_t0_runs_inline_no_approval(db: AsyncSession):
    task_id = uuid.uuid4()
    fake = _Fake([_tu("t1", "get_voip_launch_playbook", {})])

    result = await run_agent(
        PM_AGENT, client=fake, db=db, task_id=task_id, instruction="Plan Acme launch."
    )

    assert result.text == "done"
    assert result.approval_ids == []
    assert (await db.scalars(select(Approval))).all() == []
    run = (await db.scalars(select(AgentRun))).one()
    assert run.agent == "pm"
    assert run.tokens_in == 200


def test_carrier_agent_has_ocn_queueing_tool():
    """Regression: the carrier agent owns OCN (NECA — not the FCC), so it
    must have queue_filing_submission in its toolset. Without this it
    can only write prose and (as observed) suggests reassigning OCN to
    the compliance agent — which is wrong."""
    tool_names = {t.name for t in CARRIER_AGENT.tools}
    assert "queue_filing_submission" in tool_names
    # And the prompt must say OCN is the carrier's job, not compliance's.
    prompt = CARRIER_AGENT.system_prompt.lower()
    assert "ocn" in prompt
    assert "neca" in prompt
    assert "not the fcc" in prompt


async def test_carrier_ocn_filing_is_queued_not_executed(db: AsyncSession):
    """The carrier agent calling queue_filing_submission for NECA-OCN-2
    must create a pending Approval — never execute. This locks in the
    approval-gateway invariant for the OCN path specifically."""
    task_id = uuid.uuid4()
    fake = _Fake(
        [
            _tu(
                "t1",
                "queue_filing_submission",
                {
                    "form": "NECA-OCN-2",
                    "summary": "OCN application for client; LOA attached.",
                    "payload": {"filer": "Amano Telecom LLC"},
                },
            ),
        ]
    )

    result = await run_agent(
        CARRIER_AGENT,
        client=fake,
        db=db,
        task_id=task_id,
        instruction="Draft and queue the NECA-OCN-2 package.",
    )

    assert result.text == "done"
    assert len(result.approval_ids) == 1
    appr = (await db.scalars(select(Approval))).one()
    assert appr.action_type == "queue_filing_submission"
    assert appr.tier == TIER_SIGN_PAY
    assert appr.decision == DECISION_PENDING
    assert appr.payload["form"] == "NECA-OCN-2"


async def test_document_t3_is_queued_not_executed(db: AsyncSession):
    task_id = uuid.uuid4()
    fake = _Fake(
        [
            _tu("t1", "lookup_document_template", {"doc_type": "loa"}),
            _tu(
                "t2",
                "send_document_for_signature",
                {"doc_type": "LOA", "recipient": "carrier@x.com", "summary": "port"},
            ),
        ]
    )

    result = await run_agent(
        DOCUMENT_AGENT,
        client=fake,
        db=db,
        task_id=task_id,
        instruction="Draft and send the LOA.",
    )

    assert result.text == "done"
    assert len(result.approval_ids) == 1
    appr = (await db.scalars(select(Approval))).one()
    assert appr.action_type == "send_document_for_signature"
    assert appr.tier == TIER_SIGN_PAY
    assert appr.decision == DECISION_PENDING
    assert appr.payload["recipient"] == "carrier@x.com"
