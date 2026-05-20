"""read_client_intake — the tool that gives agents the ACTUAL intake
field values so their drafts contain real data, not '[from intake]'
placeholders. Five filing-drafting agents (carrier, compliance,
state_licensing, document, intake) must all have it.
"""

import json
import uuid
from dataclasses import dataclass

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.agents.carrier import CARRIER_AGENT
from app.agents.compliance import COMPLIANCE_AGENT
from app.agents.document import DOCUMENT_AGENT
from app.agents.intake import INTAKE_AGENT
from app.agents.state_licensing import STATE_LICENSING_AGENT
from app.agents.tools import ALL_TOOLS, ToolContext, read_client_intake
from app.db import Base
from app.models import Client, ClientIntake

import app.models  # noqa: F401  -- registers metadata


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


@dataclass(frozen=True)
class _FakeUser:
    id: uuid.UUID


async def _seed_client_with_intake(db: AsyncSession) -> uuid.UUID:
    from app.models.user import User  # noqa: PLC0415

    u = User(email="o@a.com", hashed_password="x", name="Op")
    db.add(u)
    await db.flush()
    c = Client(owner_id=u.id, name="Amano Telecom LLC")
    db.add(c)
    await db.flush()
    intake = ClientIntake(
        client_id=c.id,
        legal_name="Amano Telecom LLC",
        entity_type="LLC",
        formation_state="WY",
        ein="39-2196239",
        principal_address={"street": "1 Main", "city": "Cheyenne", "zip": "82001"},
        officer_name="Amber Sidney Hunt",
        officer_title="CEO",
        officer_email="amber@amano.test",
        primary_contact_name="Amber Sidney Hunt",
        primary_contact_email="amber@amano.test",
        primary_contact_phone="+13075550199",
        target_states=["WY"],
        intends_international=False,
        estimated_monthly_revenue=5000,
    )
    db.add(intake)
    await db.flush()
    return c.id


async def test_read_client_intake_returns_real_values(db: AsyncSession):
    """Smoke: agent calling the tool gets back the actual field values
    as JSON, not placeholders. This is the fix that was missing — the
    carrier agent could not previously read these."""
    client_id = await _seed_client_with_intake(db)
    ctx = ToolContext(db=db, client_id=client_id, project_id=None, task_id=uuid.uuid4())

    out = await read_client_intake.db_runner({}, ctx)
    # The text payload contains a JSON dump.
    assert "Amano Telecom LLC" in out
    assert "39-2196239" in out
    assert "Amber Sidney Hunt" in out
    # The actual JSON parses cleanly when extracted.
    body = out.split("\n", 1)[1]  # skip the prose intro
    data = json.loads(body)
    assert data["legal_name"] == "Amano Telecom LLC"
    assert data["ein"] == "39-2196239"
    assert data["officer_email"] == "amber@amano.test"
    assert data["target_states"] == ["WY"]


async def test_read_client_intake_when_no_intake_yet(db: AsyncSession):
    """Defensive: brand-new client with no intake row → agent gets a
    plain-English message it can reason about, not a Python traceback."""
    from app.models.user import User  # noqa: PLC0415

    u = User(email="o2@a.com", hashed_password="x", name="Op2")
    db.add(u)
    await db.flush()
    c = Client(owner_id=u.id, name="Brand New Co")
    db.add(c)
    await db.flush()

    ctx = ToolContext(db=db, client_id=c.id, project_id=None, task_id=uuid.uuid4())
    out = await read_client_intake.db_runner({}, ctx)
    assert "No intake row" in out


async def test_read_client_intake_no_client_context(db: AsyncSession):
    """Defensive: tool called with no client_id (shouldn't happen in
    practice, but the path must stay safe)."""
    ctx = ToolContext(db=db, client_id=None, project_id=None, task_id=uuid.uuid4())
    out = await read_client_intake.db_runner({}, ctx)
    assert "No client context" in out


def test_read_client_intake_registered_in_all_tools():
    assert "read_client_intake" in ALL_TOOLS
    assert ALL_TOOLS["read_client_intake"] is read_client_intake


def test_filing_drafting_agents_have_read_client_intake():
    """Carrier, compliance, state_licensing, document, and intake all
    draft content the operator never re-types — they MUST be able to
    read the actual values. PM has its own check_intake_status path
    (completeness only) and doesn't need raw values."""
    for spec in (
        CARRIER_AGENT,
        COMPLIANCE_AGENT,
        STATE_LICENSING_AGENT,
        DOCUMENT_AGENT,
        INTAKE_AGENT,
    ):
        names = {t.name for t in spec.tools}
        assert (
            "read_client_intake" in names
        ), f"{spec.name} is missing read_client_intake"


def test_carrier_prompt_calls_read_client_intake_first():
    """Locks in the v1.3.12 hardening: carrier prompt explicitly says
    read_client_intake is step 1 of the OCN drafting flow. If a
    future edit drops this, drafts will regress to '[from intake]'
    placeholders again."""
    p = CARRIER_AGENT.system_prompt
    assert "read_client_intake" in p
    assert "FIRST" in p  # ordering directive
    # And the existing v1.3.11 invariants must still hold.
    assert "lookup_frn" in p
    assert "BIAS TOWARD ACTION" in p
