"""Per-filing stage tracker. The platform auto-derives the required
launch applications from the client's intake, exposes them as a list
the operator can update, and gives agents a tool to self-report
progress. OCN is wired as a first-class application type so the
operator's dashboard surfaces 'NECA application pending'.
"""

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.agents.tools import ToolContext, update_application_stage
from app.applications import MANDATORY_FIVE, resolve_required_applications
from app.db import Base, get_db
from app.main import app
from app.models import Application, ClientIntake


_FULL_INTAKE_BASE = {
    "legal_name": "Acme VoIP LLC",
    "entity_type": "LLC",
    "formation_state": "DE",
    "ein": "99-1234567",
    "principal_address": {"street": "1 Main", "city": "Austin", "zip": "78701"},
    "officer_name": "Jane Roe",
    "officer_title": "CEO",
    "officer_email": "jane@acme.example",
    "primary_contact_name": "Jane Roe",
    "primary_contact_email": "jane@acme.example",
    "primary_contact_phone": "+15125550100",
    "estimated_monthly_revenue": 25000,
}


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


async def _auth(c: AsyncClient) -> dict:
    r = await c.post(
        "/auth/register",
        json={"email": "op@example.com", "password": "supersecret", "name": "Op"},
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_resolve_pre_formation_returns_founder_set_only():
    intake = ClientIntake(client_id=uuid.uuid4())
    needed = resolve_required_applications(intake)
    assert set(needed) == {"entity_formation", "ein", "bank_account"}


def test_resolve_entity_stage_includes_mandatory_five_and_per_state():
    """Every US voice carrier must complete the five FCC/NECA filings:
    CORES (FRN), OCN, FCC 499, RMD, STIR/SHAKEN — in that order. Each
    target state also pulls in a state CPCN row."""
    intake = ClientIntake(
        client_id=uuid.uuid4(),
        ein="99-1234567",
        target_states=["TX", "CA"],
        intends_international=False,
    )
    needed = resolve_required_applications(intake)
    # The mandatory five — order preserved in the returned list.
    for app_type in MANDATORY_FIVE:
        assert app_type in needed, f"missing mandatory {app_type}"
    # CORES (FRN) is FIRST among the mandatory five so the operator
    # gets the FRN before NECA / 499 / RMD references ask for it.
    five_indexes = [needed.index(t) for t in MANDATORY_FIVE]
    assert five_indexes == sorted(five_indexes), (
        "Mandatory five must appear in dependency order"
    )
    assert needed.index("cores_frn") < needed.index("ocn")
    assert needed.index("ocn") < needed.index("rmd")
    assert needed.index("rmd") < needed.index("stir_shaken")
    # State CPCNs come along too.
    assert "state_cpcn:TX" in needed
    assert "state_cpcn:CA" in needed
    # No international → no Section 214.
    assert "section_214" not in needed


def test_resolve_international_pulls_section_214():
    intake = ClientIntake(
        client_id=uuid.uuid4(),
        ein="99-1234567",
        target_states=["NY"],
        intends_international=True,
    )
    needed = resolve_required_applications(intake)
    assert "section_214" in needed


async def test_sync_from_intake_creates_required_apps_and_is_idempotent(ctx):
    c, maker = ctx
    h = await _auth(c)
    cid = (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    await c.put(
        f"/clients/{cid}/intake",
        headers=h,
        json={**_FULL_INTAKE_BASE, "target_states": ["TX", "CA"]},
    )

    r = await c.post(f"/clients/{cid}/applications/sync", headers=h)
    assert r.status_code == 200
    created = set(r.json()["created"])
    # The whole mandatory five lands plus per-state CPCN rows.
    assert {"cores_frn", "ocn", "fcc_499", "rmd", "stir_shaken"} <= created
    assert {"state_cpcn:TX", "state_cpcn:CA"} <= created

    # List shows them with derived labels + default agent.
    r = await c.get(f"/clients/{cid}/applications", headers=h)
    rows = r.json()
    by_type = {row["type"]: row for row in rows}
    assert by_type["ocn"]["label"] == "OCN (NECA)"
    assert by_type["ocn"]["default_agent"] == "carrier"
    assert by_type["fcc_499"]["default_agent"] == "compliance"
    assert by_type["state_cpcn:TX"]["default_agent"] == "state_licensing"

    # Re-syncing is a no-op — nothing new created, existing preserved.
    r = await c.post(f"/clients/{cid}/applications/sync", headers=h)
    assert r.json()["created"] == []

    # Adding a target state and re-syncing picks up the new state row only.
    await c.put(
        f"/clients/{cid}/intake",
        headers=h,
        json={**_FULL_INTAKE_BASE, "target_states": ["TX", "CA", "NY"]},
    )
    r = await c.post(f"/clients/{cid}/applications/sync", headers=h)
    assert set(r.json()["created"]) == {"state_cpcn:NY"}


async def test_operator_can_update_stage_with_audit(ctx):
    c, maker = ctx
    h = await _auth(c)
    cid = (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    await c.put(f"/clients/{cid}/intake", headers=h, json=_FULL_INTAKE_BASE)
    await c.post(f"/clients/{cid}/applications/sync", headers=h)
    rows = (await c.get(f"/clients/{cid}/applications", headers=h)).json()
    ocn = next(r for r in rows if r["type"] == "ocn")

    r = await c.patch(
        f"/clients/{cid}/applications/{ocn['id']}",
        headers=h,
        json={
            "stage": "submitted",
            "external_ref": "NECA-ACK-12345",
            "notes": "Submitted via NECA-OCN-2. Confirmation email received.",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["stage"] == "submitted"
    assert body["external_ref"] == "NECA-ACK-12345"


async def test_invalid_stage_is_rejected(ctx):
    c, _ = ctx
    h = await _auth(c)
    cid = (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    r = await c.post(
        f"/clients/{cid}/applications",
        headers=h,
        json={"type": "carrier:bandwidth"},
    )
    aid = r.json()["id"]
    r = await c.patch(
        f"/clients/{cid}/applications/{aid}",
        headers=h,
        json={"stage": "wildly-made-up"},
    )
    assert r.status_code == 400


async def test_other_operator_cannot_see_or_edit(ctx):
    c, _ = ctx
    h_a = await _auth(c)
    cid = (await c.post("/clients", headers=h_a, json={"name": "A"})).json()["id"]
    await c.post(
        f"/clients/{cid}/applications",
        headers=h_a,
        json={"type": "ocn"},
    )
    # Different operator.
    r = await c.post(
        "/auth/register",
        json={"email": "b@example.com", "password": "supersecret", "name": "B"},
    )
    hb = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = await c.get(f"/clients/{cid}/applications", headers=hb)
    assert r.status_code == 404


async def test_agent_tool_updates_stage_and_sets_current_agent(ctx):
    """The killer feature: an agent self-reports its progress, and the
    operator's UI immediately shows 'compliance is working on FCC 499'."""
    c, maker = ctx
    h = await _auth(c)
    cid_resp = (
        await c.post("/clients", headers=h, json={"name": "Acme"})
    ).json()
    cid = uuid.UUID(cid_resp["id"])
    await c.put(f"/clients/{cid}/intake", headers=h, json=_FULL_INTAKE_BASE)
    await c.post(f"/clients/{cid}/applications/sync", headers=h)

    async with maker() as s:
        tool_ctx = ToolContext(
            db=s,
            client_id=cid,
            project_id=None,
            task_id=uuid.uuid4(),
            agent_name="compliance",
        )
        result = await update_application_stage.db_runner(
            {
                "application_type": "fcc_499",
                "stage": "in_progress",
                "note": "Drafting 499-A based on captured intake.",
            },
            tool_ctx,
        )
        assert "Updated fcc_499" in result
        await s.commit()

    rows = (await c.get(f"/clients/{cid}/applications", headers=h)).json()
    fcc = next(r for r in rows if r["type"] == "fcc_499")
    assert fcc["stage"] == "in_progress"
    assert fcc["current_agent"] == "compliance"
    assert "Drafting 499-A" in fcc["notes"]


async def test_agent_tool_rejects_unknown_application(ctx):
    c, maker = ctx
    h = await _auth(c)
    cid_resp = (
        await c.post("/clients", headers=h, json={"name": "Acme"})
    ).json()
    cid = uuid.UUID(cid_resp["id"])
    async with maker() as s:
        tool_ctx = ToolContext(
            db=s,
            client_id=cid,
            project_id=None,
            task_id=uuid.uuid4(),
            agent_name="compliance",
        )
        result = await update_application_stage.db_runner(
            {"application_type": "never_existed", "stage": "complete"},
            tool_ctx,
        )
        assert "No application of type" in result
