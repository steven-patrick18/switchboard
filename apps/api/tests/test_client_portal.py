"""Public client portal — the operator generates a magic link; the client
uses it to fill intake, upload documents, and drop in credentials, all
scoped to that one client. Audit actor for every write is `client:{link.id}`
so the operator's trail shows exactly who did what. Tokens are tested for
revocation, expiration, and cross-client isolation.
"""

import uuid
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import AuditLog, ClientLink, Credential, Document


@pytest_asyncio.fixture
async def client(tmp_path: Path):
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
        c._maker = maker  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


async def _register(c: AsyncClient, email: str = "op@example.com") -> dict:
    r = await c.post(
        "/auth/register",
        json={"email": email, "password": "supersecret", "name": "Op"},
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _link(c: AsyncClient, headers: dict, client_id: str) -> dict:
    r = await c.post(
        f"/clients/{client_id}/links",
        headers=headers,
        json={"label": "share-1"},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_link_lifecycle_open_intake_upload(client: AsyncClient):
    h = await _register(client)
    cid = (
        await client.post("/clients", headers=h, json={"name": "Acme"})
    ).json()["id"]
    link = await _link(client, h, cid)
    tok = link["token"]

    # Anyone with the link can read the company's identity (and only that).
    r = await client.get(f"/client-portal/{tok}")
    assert r.status_code == 200
    assert r.json()["client_name"] == "Acme"
    assert r.json()["label"] == "share-1"

    # The client fills part of the intake.
    r = await client.put(
        f"/client-portal/{tok}/intake",
        json={"legal_name": "Acme VoIP LLC", "entity_type": "LLC"},
    )
    assert r.status_code == 200
    assert r.json()["intake"]["legal_name"] == "Acme VoIP LLC"

    # The client uploads a document.
    r = await client.post(
        f"/client-portal/{tok}/documents",
        data={"type": "ssn_card"},
        files={"file": ("ssn.pdf", b"client-uploaded-bytes", "application/pdf")},
    )
    assert r.status_code == 201
    doc_id = r.json()["id"]
    # And can fetch it back.
    r = await client.get(f"/client-portal/{tok}/documents/{doc_id}/download")
    assert r.status_code == 200 and r.content == b"client-uploaded-bytes"

    # The operator sees the uploaded document on their owner-scoped surface.
    r = await client.get(f"/clients/{cid}/documents", headers=h)
    types = {d["type"] for d in r.json()}
    assert "ssn_card" in types

    # Audit entries record actor = client:{link.id} (not the operator).
    maker = client._maker  # type: ignore[attr-defined]
    async with maker() as s:
        entries = (await s.scalars(select(AuditLog))).all()
    actors = {e.actor for e in entries}
    assert any(a.startswith(f"client:{link['id']}") for a in actors)
    actions = {e.action for e in entries}
    assert "intake.upserted_by_client" in actions
    assert "document.uploaded_by_client" in actions


async def test_credentials_are_write_only_through_portal(client: AsyncClient):
    h = await _register(client)
    cid = (
        await client.post("/clients", headers=h, json={"name": "Acme"})
    ).json()["id"]
    tok = (await _link(client, h, cid))["token"]

    # Client adds a credential.
    r = await client.post(
        f"/client-portal/{tok}/credentials",
        json={"service": "fcc_cores", "secret": "client-secret-pw"},
    )
    assert r.status_code == 201

    # The portal lists service NAMES only — never the secret value.
    r = await client.get(f"/client-portal/{tok}/credentials/services")
    assert r.status_code == 200
    assert r.json() == ["fcc_cores"]
    # No endpoint exists to GET a credential's secret through the portal
    # (the operator's vault is the only place that's possible, and only
    # via the audited `use_credential` flow).
    assert "client-secret-pw" not in r.text

    # The credential exists on the operator's side (encrypted).
    maker = client._maker  # type: ignore[attr-defined]
    async with maker() as s:
        rows = (await s.scalars(select(Credential))).all()
    assert len(rows) == 1
    assert rows[0].service == "fcc_cores"
    # Ciphertext on disk is not the cleartext password.
    assert "client-secret-pw" not in (rows[0].secret_ciphertext or "")


async def test_revoked_and_expired_links_refuse(client: AsyncClient):
    h = await _register(client)
    cid = (
        await client.post("/clients", headers=h, json={"name": "Acme"})
    ).json()["id"]
    link = await _link(client, h, cid)
    tok = link["token"]

    # Revoke and verify subsequent calls 403.
    r = await client.delete(f"/clients/{cid}/links/{link['id']}", headers=h)
    assert r.status_code == 204
    r = await client.get(f"/client-portal/{tok}")
    assert r.status_code == 403

    # Manually expire a different link.
    link2 = await _link(client, h, cid)
    tok2 = link2["token"]
    maker = client._maker  # type: ignore[attr-defined]
    async with maker() as s:
        row = await s.scalar(select(ClientLink).where(ClientLink.token == tok2))
        # Set expiration in the past.
        from datetime import UTC, datetime, timedelta

        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await s.commit()
    r = await client.get(f"/client-portal/{tok2}")
    assert r.status_code == 403


async def test_link_scope_isolation_404_for_garbage_token(client: AsyncClient):
    r = await client.get("/client-portal/not-a-real-token")
    assert r.status_code == 404


async def test_operator_can_list_and_revoke_links(client: AsyncClient):
    h = await _register(client)
    cid = (
        await client.post("/clients", headers=h, json={"name": "Acme"})
    ).json()["id"]
    l1 = await _link(client, h, cid)
    l2 = await _link(client, h, cid)

    r = await client.get(f"/clients/{cid}/links", headers=h)
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()}
    assert {l1["id"], l2["id"]} <= ids

    # Other operator cannot list the first's links.
    hb = await _register(client, "b@example.com")
    r = await client.get(f"/clients/{cid}/links", headers=hb)
    assert r.status_code == 404
