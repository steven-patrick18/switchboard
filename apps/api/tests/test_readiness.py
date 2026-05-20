"""Launch readiness: given intake + applications + their stages, the
platform tells the operator exactly what they can start right now and
what's blocked on what. Deterministic; no LLM cost.
"""

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import Application
from app.models.application import STAGE_COMPLETE, STAGE_IN_PROGRESS

_FULL_INTAKE = {
    "legal_name": "Amano Telecom LLC",
    "entity_type": "LLC",
    "formation_state": "WY",
    "ein": "39-2196239",
    "principal_address": {"street": "1 Main", "city": "Cheyenne", "zip": "82001"},
    "officer_name": "Amber Sidney Hunt",
    "officer_title": "Member",
    "officer_email": "amber@amano.example",
    "primary_contact_name": "Amber Sidney Hunt",
    "primary_contact_email": "amber@amano.example",
    "primary_contact_phone": "+13075550100",
    "target_states": ["TX"],
    "intends_international": False,
    "estimated_monthly_revenue": 25000,
}


@pytest_asyncio.fixture
async def ctx(tmp_path):
    settings.documents_dir = str(tmp_path / "docs")
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


async def _set_stage(maker, client_id, app_type, stage):
    async with maker() as s:
        await s.execute(
            update(Application)
            .where(Application.client_id == client_id, Application.type == app_type)
            .values(stage=stage)
        )
        await s.commit()


async def test_readiness_locks_until_intake_complete(ctx):
    c, _ = ctx
    h = await _auth(c)
    cid = (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    # No intake → readiness reports intake_complete=False, all empty.
    r = await c.get(f"/clients/{cid}/applications/readiness", headers=h)
    body = r.json()
    assert body["intake_complete"] is False
    assert body["intake_missing_fields"]


async def _seed_client_and_sync(c: AsyncClient, h, name="Acme"):
    cid = (await c.post("/clients", headers=h, json={"name": name})).json()["id"]
    await c.put(f"/clients/{cid}/intake", headers=h, json=_FULL_INTAKE)
    # Upload the four mandatory intake-phase docs.
    for key in (
        "formation_certificate",
        "ein_letter",
        "officer_government_id",
        "proof_of_address",
    ):
        await c.post(
            f"/clients/{cid}/documents",
            headers=h,
            data={"type": key},
            files={"file": (f"{key}.pdf", b"stub", "application/pdf")},
        )
    await c.post(f"/clients/{cid}/applications/sync", headers=h)
    return cid


async def test_cores_frn_is_ready_after_intake(ctx):
    """The first filing in the mandatory five — CORES has no prereqs
    other than intake completeness, so it should be the first thing
    operators see in the Ready list."""
    c, _ = ctx
    h = await _auth(c)
    cid = await _seed_client_and_sync(c, h)
    r = await c.get(f"/clients/{cid}/applications/readiness", headers=h)
    body = r.json()
    assert body["intake_complete"] is True
    ready_types = {it["application_type"] for it in body["ready"]}
    # CORES + state CPCN + early entity-stage items are ready.
    assert "cores_frn" in ready_types
    assert "state_cpcn:TX" in ready_types
    # Templated instruction is wired with the right owner agent.
    cores = next(it for it in body["ready"] if it["application_type"] == "cores_frn")
    assert cores["owner_agent"] == "compliance"
    assert "CORES" in cores["instruction"]


async def test_ocn_blocked_until_cores_complete(ctx):
    c, maker = ctx
    h = await _auth(c)
    cid = await _seed_client_and_sync(c, h)

    r = await c.get(f"/clients/{cid}/applications/readiness", headers=h)
    body = r.json()
    blocked_types = {it["application_type"] for it in body["blocked"]}
    assert "ocn" in blocked_types
    assert "rmd" in blocked_types
    assert "stir_shaken" in blocked_types

    ocn = next(it for it in body["blocked"] if it["application_type"] == "ocn")
    assert any("CORES" in reason for reason in ocn["blocked_on"])

    # Complete CORES → OCN unlocks.
    await _set_stage(maker, uuid.UUID(cid), "cores_frn", STAGE_COMPLETE)
    r = await c.get(f"/clients/{cid}/applications/readiness", headers=h)
    body = r.json()
    ready_types = {it["application_type"] for it in body["ready"]}
    assert "ocn" in ready_types
    # 499 also unlocks (it depends only on CORES).
    assert "fcc_499" in ready_types
    # RMD is still blocked — needs OCN, which is now ready but not complete.
    blocked_types = {it["application_type"] for it in body["blocked"]}
    assert "rmd" in blocked_types


async def test_stir_shaken_unlocks_only_when_both_ocn_and_rmd_complete(ctx):
    c, maker = ctx
    h = await _auth(c)
    cid = await _seed_client_and_sync(c, h)
    cid_uuid = uuid.UUID(cid)
    await _set_stage(maker, cid_uuid, "cores_frn", STAGE_COMPLETE)
    await _set_stage(maker, cid_uuid, "ocn", STAGE_COMPLETE)
    # Only OCN done — STIR/SHAKEN still needs RMD.
    r = await c.get(f"/clients/{cid}/applications/readiness", headers=h)
    body = r.json()
    blocked = {it["application_type"]: it for it in body["blocked"]}
    assert "stir_shaken" in blocked
    assert any("Robocall" in reason for reason in blocked["stir_shaken"]["blocked_on"])

    # Now also complete RMD → STIR/SHAKEN is ready.
    await _set_stage(maker, cid_uuid, "rmd", STAGE_COMPLETE)
    r = await c.get(f"/clients/{cid}/applications/readiness", headers=h)
    body = r.json()
    ready = {it["application_type"] for it in body["ready"]}
    assert "stir_shaken" in ready


async def test_in_flight_items_dont_show_as_ready(ctx):
    """An app the agent is already drafting shouldn't reappear in
    Ready — that would invite double-kicking the same task."""
    c, maker = ctx
    h = await _auth(c)
    cid = await _seed_client_and_sync(c, h)
    await _set_stage(maker, uuid.UUID(cid), "cores_frn", STAGE_IN_PROGRESS)
    r = await c.get(f"/clients/{cid}/applications/readiness", headers=h)
    body = r.json()
    ready_types = {it["application_type"] for it in body["ready"]}
    assert "cores_frn" not in ready_types
    in_flight_types = {it["application_type"] for it in body["in_flight"]}
    assert "cores_frn" in in_flight_types


async def test_other_operator_cannot_read_readiness(ctx):
    c, _ = ctx
    h_a = await _auth(c)
    cid = (await c.post("/clients", headers=h_a, json={"name": "A"})).json()["id"]
    r = await c.post(
        "/auth/register",
        json={"email": "b@example.com", "password": "supersecret", "name": "B"},
    )
    hb = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = await c.get(f"/clients/{cid}/applications/readiness", headers=hb)
    assert r.status_code == 404
