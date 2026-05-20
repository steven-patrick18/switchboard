"""Agents learn from operator corrections. When an approval is
rejected with a reason or edited before approval, an AgentLesson is
captured tagged with the agent that queued the action. On the next
agent run, those lessons are injected into the system prompt so the
model sees its own past mistakes in context.
"""

import uuid
from dataclasses import dataclass

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.agents.base import run_agent
from app.agents.registry import COMPLIANCE_AGENT
from app.agent_learning import fetch_recent_lessons, format_lessons_block
from app.db import Base, get_db
from app.llm import get_anthropic_client
from app.main import app
from app.models import AgentLesson, Approval, Project, Task
from app.models.agent_lesson import SOURCE_EDIT, SOURCE_REJECTION
from app.models.approval import DECISION_PENDING, TIER_SIGN_PAY


@dataclass
class _Block:
    text: str
    type: str = "text"


@dataclass
class _Usage:
    input_tokens: int = 10
    output_tokens: int = 5


@dataclass
class _Response:
    content: list
    stop_reason: str
    usage: _Usage


class _FakeAnthropic:
    """Captures the system prompt of every messages.create call so the
    test can assert that lessons reached the model."""

    def __init__(self):
        self.calls: list[dict] = []

    @property
    def messages(self):
        return self

    async def create(self, **kw):
        self.calls.append(kw)
        return _Response(
            content=[_Block(text="ok, nothing to do")],
            stop_reason="end_turn",
            usage=_Usage(),
        )


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


async def _seed_approval(maker, client_id, *, action_type: str, agent: str) -> uuid.UUID:
    """Approval comes from a Task tagged with the agent that queued it."""
    async with maker() as s:
        project = Project(client_id=client_id)
        s.add(project)
        await s.flush()
        task = Task(project_id=project.id, agent=agent, status="awaiting_approval")
        s.add(task)
        await s.flush()
        appr = Approval(
            task_id=task.id,
            action_type=action_type,
            tier=TIER_SIGN_PAY,
            payload={"form": "FCC 499-A", "draft": "v1"},
            decision=DECISION_PENDING,
        )
        s.add(appr)
        await s.flush()
        aid = appr.id
        await s.commit()
        return aid


async def _register(c: AsyncClient, email: str = "op@example.com") -> dict:
    r = await c.post(
        "/auth/register",
        json={"email": email, "password": "supersecret", "name": "Op"},
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_rejection_records_a_lesson_tagged_to_the_agent(ctx):
    c, maker = ctx
    h = await _register(c)
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    )
    aid = await _seed_approval(
        maker, cid, action_type="queue_filing_submission", agent="compliance"
    )

    r = await c.post(
        f"/approvals/{aid}/reject",
        headers=h,
        json={"reason": "Wrong officer named; use the CEO not the CFO."},
    )
    assert r.status_code == 200

    async with maker() as s:
        rows = list((await s.scalars(select(AgentLesson))).all())
    assert len(rows) == 1
    lsn = rows[0]
    assert lsn.agent_name == "compliance"
    assert lsn.source == SOURCE_REJECTION
    assert lsn.action_type == "queue_filing_submission"
    assert "CEO not the CFO" in lsn.lesson
    assert lsn.source_approval_id == aid


async def test_edit_records_a_lesson_with_payload_diff(ctx):
    c, maker = ctx
    h = await _register(c)
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    )
    aid = await _seed_approval(
        maker, cid, action_type="queue_filing_submission", agent="compliance"
    )

    r = await c.post(
        f"/approvals/{aid}/approve",
        headers=h,
        json={
            "payload_override": {"form": "FCC 499-A", "draft": "v2-officer-CEO"},
            "note": "Fixed officer name before approving.",
        },
    )
    assert r.status_code == 200

    async with maker() as s:
        rows = list((await s.scalars(select(AgentLesson))).all())
    assert len(rows) == 1
    lsn = rows[0]
    assert lsn.source == SOURCE_EDIT
    assert lsn.agent_name == "compliance"
    # Lesson mentions the changed field.
    assert "draft" in lsn.lesson
    assert "Fixed officer name" in lsn.lesson


async def test_plain_approve_does_not_record_a_lesson(ctx):
    c, maker = ctx
    h = await _register(c)
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    )
    aid = await _seed_approval(
        maker, cid, action_type="queue_filing_submission", agent="compliance"
    )
    r = await c.post(f"/approvals/{aid}/approve", headers=h, json={})
    assert r.status_code == 200
    async with maker() as s:
        assert (await s.scalars(select(AgentLesson))).all() == []


async def test_lessons_are_injected_into_the_system_prompt(ctx):
    """The real test: after a rejection, the next run_agent call for
    the same agent + operator must see the lesson in its system prompt."""
    c, maker = ctx
    h = await _register(c)
    me = (await c.get("/auth/me", headers=h)).json()
    owner_id = uuid.UUID(me["id"])
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    )

    # Reject one approval → captures a lesson.
    aid = await _seed_approval(
        maker, cid, action_type="queue_filing_submission", agent="compliance"
    )
    await c.post(
        f"/approvals/{aid}/reject",
        headers=h,
        json={"reason": "Never file before EIN is captured."},
    )

    # Now run the compliance agent → its system prompt must contain
    # the captured lesson.
    fake = _FakeAnthropic()
    async with maker() as s:
        task = Task(
            project_id=(
                await s.scalar(select(Project).where(Project.client_id == cid))
            ).id,
            agent="compliance",
            status="running",
            input={"instruction": "research only"},
        )
        s.add(task)
        await s.flush()
        await run_agent(
            COMPLIANCE_AGENT,
            client=fake,
            db=s,
            task_id=task.id,
            instruction="research only",
            client_id=cid,
            project_id=task.project_id,
            owner_id=owner_id,
        )
        await s.commit()

    assert len(fake.calls) == 1
    system_blocks = fake.calls[0]["system"]
    # System is a list of blocks; the lesson block comes first (uncached).
    assert system_blocks[0]["type"] == "text"
    assert "PAST CORRECTIONS" in system_blocks[0]["text"]
    assert "Never file before EIN" in system_blocks[0]["text"]
    # The agent's frozen prompt is still there as a cached block.
    assert any(
        "cache_control" in b and "Compliance Agent" in b["text"]
        for b in system_blocks
    )


async def test_lessons_are_owner_scoped(ctx):
    """Operator A's correction must not leak into operator B's agent run."""
    c, maker = ctx
    ha = await _register(c, "a@example.com")
    hb = await _register(c, "b@example.com")
    aid_user = uuid.UUID((await c.get("/auth/me", headers=ha)).json()["id"])
    bid_user = uuid.UUID((await c.get("/auth/me", headers=hb)).json()["id"])
    ca = uuid.UUID(
        (await c.post("/clients", headers=ha, json={"name": "A"})).json()["id"]
    )
    aa = await _seed_approval(
        maker, ca, action_type="queue_filing_submission", agent="compliance"
    )
    await c.post(
        f"/approvals/{aa}/reject", headers=ha, json={"reason": "A-only-lesson"}
    )

    async with maker() as s:
        a_lessons = await fetch_recent_lessons(
            s, owner_id=aid_user, agent_name="compliance"
        )
        b_lessons = await fetch_recent_lessons(
            s, owner_id=bid_user, agent_name="compliance"
        )
    assert any("A-only-lesson" in l.lesson for l in a_lessons)
    assert b_lessons == []  # operator B sees nothing


async def test_lesson_endpoints_list_add_delete(ctx):
    c, _ = ctx
    h = await _register(c)
    # No lessons yet.
    r = await c.get("/agents/compliance/lessons", headers=h)
    assert r.status_code == 200 and r.json() == []

    # Add a manual lesson.
    r = await c.post(
        "/agents/compliance/lessons",
        headers=h,
        json={"lesson": "Always cite the FCC rule by section number."},
    )
    assert r.status_code == 201
    lid = r.json()["id"]
    assert r.json()["source"] == "manual"

    # Lesson count flows into /agents.
    r = await c.get("/agents", headers=h)
    by_name = {a["name"]: a for a in r.json()}
    assert by_name["compliance"]["lesson_count"] == 1

    # Delete.
    r = await c.delete(f"/agents/lessons/{lid}", headers=h)
    assert r.status_code == 204
    r = await c.get("/agents/compliance/lessons", headers=h)
    assert r.json() == []


def test_empty_lessons_block_is_empty_string():
    """No lessons → no injection, so the operator's clean prompt stays clean."""
    assert format_lessons_block([]) == ""
