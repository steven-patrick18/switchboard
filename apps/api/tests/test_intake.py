"""The capture-once mandate: every required datum and document is gathered
at onboarding, and a client cannot be submitted until complete — so the
client is never re-disturbed mid-process. Full HTTP flow, in-memory DB.
"""

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.intake import mandatory_document_keys
from app.main import app  # importing app.main registers all model metadata


@pytest_asyncio.fixture
async def client(tmp_path: Path):
    # Isolate document storage per test so disk writes don't leak.
    settings.documents_dir = str(tmp_path / "docs")

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


async def _auth(c: AsyncClient) -> dict:
    r = await c.post(
        "/auth/register",
        json={"email": "op@example.com", "password": "supersecret", "name": "Op"},
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_capture_once_mandate(client: AsyncClient):
    h = await _auth(client)

    r = await client.post("/clients", headers=h, json={"name": "Acme VoIP"})
    cid = r.json()["id"]

    # Nothing captured yet → everything missing, not complete.
    r = await client.get(f"/clients/{cid}/intake", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["intake"] is None
    assert body["completeness"]["complete"] is False
    assert "legal_name" in body["completeness"]["missing_fields"]
    # No intake yet → the base mandated set is decided.
    assert set(body["completeness"]["missing_documents"]) == set(
        mandatory_document_keys(None)
    )

    # Submitting an incomplete intake is blocked (the mandate).
    r = await client.post(f"/clients/{cid}/intake/submit", headers=h)
    assert r.status_code == 409
    assert r.json()["detail"]["missing_fields"]

    # Capture all required fields.
    r = await client.put(
        f"/clients/{cid}/intake",
        headers=h,
        json={
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
            "target_states": ["TX", "CA"],
            "estimated_monthly_revenue": 25000,
        },
    )
    assert r.status_code == 200
    comp = r.json()["completeness"]
    assert comp["missing_fields"] == []
    # Docs still outstanding → still not complete, submit still blocked.
    assert comp["complete"] is False

    # The mandated set is decided per client. target_states pulls in
    # the notarized state CPCN packet, but it's PHASE_LAUNCH — the
    # operator drafts it for the client to sign DURING the state
    # filing, not at intake time, so it's listed but NOT a blocker.
    req = {d["key"]: d for d in comp["required_documents"]}
    assert "state_cpcn_notarized" in req
    assert req["state_cpcn_notarized"]["needs_scan"] is True
    assert req["state_cpcn_notarized"]["phase"] == "launch"
    assert req["state_cpcn_notarized"]["mandatory"] is False
    assert req["officer_government_id"]["needs_scan"] is True
    assert req["officer_government_id"]["phase"] == "intake"
    assert req["officer_government_id"]["mandatory"] is True
    assert req["ein_letter"]["needs_scan"] is False
    # The deferred set: things acquired during the launch, not at intake.
    deferred = {d["key"] for d in comp["required_documents"] if not d["mandatory"]}
    assert {"banking_letter", "corporate_authorization", "state_cpcn_notarized"} <= deferred

    r = await client.post(f"/clients/{cid}/intake/submit", headers=h)
    assert r.status_code == 409  # still blocked — intake-phase docs missing

    # Upload ONLY the intake-phase (mandatory) docs.
    intake_phase = [d for d in comp["required_documents"] if d["mandatory"]]
    for d in intake_phase:
        r = await client.post(
            f"/clients/{cid}/documents",
            headers=h,
            data={"type": d["key"]},
            files={"file": (f"{d['key']}.pdf", b"%PDF-1.4 stub", "application/pdf")},
        )
        assert r.status_code == 201

    r = await client.get(f"/clients/{cid}/intake", headers=h)
    # Intake is complete even though deferred docs (banking_letter,
    # corporate_authorization, state_cpcn_notarized) haven't been
    # uploaded — those come during the launch.
    assert r.json()["completeness"]["complete"] is True

    # Now the client can be locked as fully onboarded.
    r = await client.post(f"/clients/{cid}/intake/submit", headers=h)
    assert r.status_code == 200
    assert r.json()["intake"]["submitted_at"] is not None

    # Workspace isolation still holds for intake.
    r2 = await client.post(
        "/auth/register",
        json={"email": "b@example.com", "password": "supersecret", "name": "B"},
    )
    hb = {"Authorization": f"Bearer {r2.json()['access_token']}"}
    r = await client.get(f"/clients/{cid}/intake", headers=hb)
    assert r.status_code == 404
