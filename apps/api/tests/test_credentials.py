"""Per-client credential vault: encrypted at rest, rotated by upsert,
audited on every access. Secrets are write-only via the API and never
appear in any response or in agent/model context. Pure tests + HTTP,
no API spend.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.agents.base import run_agent
from app.agents.compliance import COMPLIANCE_AGENT
from app.crypto import decrypt, encrypt
from app.db import Base, get_db
from app.llm import get_anthropic_client
from app.main import app
from app.models import AuditLog, Credential
from app.vault import (
    credential_availability,
    list_credentials,
    store_credential,
    use_credential,
)


def test_crypto_round_trip_and_tamper():
    token = encrypt("hunter2")
    assert token != "hunter2"
    assert decrypt(token) == "hunter2"
    # Garbage ciphertext / wrong key surface as ValueError, not silent.
    with pytest.raises(ValueError):
        decrypt("not-a-real-token")


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


async def test_store_rotate_use_and_audit(db: AsyncSession):
    cid = uuid.uuid4()

    cred = await store_credential(
        db, client_id=cid, service="fcc_cores", secret="TOPSECRET", username="op"
    )
    await db.commit()
    assert cred.secret_ciphertext != "TOPSECRET"  # not plaintext

    # Rotate (upsert) — same row, new secret.
    cred2 = await store_credential(
        db, client_id=cid, service="fcc_cores", secret="NEW_SECRET"
    )
    await db.commit()
    assert cred2.id == cred.id
    all_rows = await list_credentials(db, cid)
    assert len(all_rows) == 1

    # Server-side use returns plaintext and audits.
    secret = await use_credential(
        db, client_id=cid, service="fcc_cores", actor="system", purpose="login"
    )
    await db.commit()
    assert secret == "NEW_SECRET"
    audits = (await db.scalars(select(AuditLog))).all()
    assert any(
        a.action == "credential.accessed" and a.actor == "system" for a in audits
    )
    refreshed = (await db.scalars(select(Credential))).one()
    assert refreshed.last_accessed_at is not None

    # Expired credential is refused.
    await store_credential(
        db,
        client_id=cid,
        service="fcc_cores",
        secret="x",
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    await db.commit()
    with pytest.raises(LookupError):
        await use_credential(
            db, client_id=cid, service="fcc_cores", actor="system", purpose="x"
        )

    # Missing service: refused.
    with pytest.raises(LookupError):
        await use_credential(
            db, client_id=cid, service="nope", actor="system", purpose="x"
        )

    # Availability lookup audits but never returns secrets.
    avail = await credential_availability(db, cid, actor="compliance")
    await db.commit()
    assert all("secret" not in row for row in avail)
    assert any(
        a.action == "credential.listed" and a.actor == "compliance"
        for a in (await db.scalars(select(AuditLog))).all()
    )


@pytest_asyncio.fixture
async def http():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _odb():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = _odb
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c, maker
    app.dependency_overrides.clear()
    await engine.dispose()


async def test_credential_endpoints_never_leak_secret(http):
    c, _maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op@acme.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]

    r = await c.post(
        f"/clients/{cid}/credentials",
        headers=h,
        json={"service": "fcc_cores", "username": "op", "secret": "TOPSECRET"},
    )
    assert r.status_code == 201
    body = r.json()
    assert "secret" not in body and "secret_ciphertext" not in body
    cred_id = body["id"]

    r = await c.get(f"/clients/{cid}/credentials", headers=h)
    raw = r.text
    assert "TOPSECRET" not in raw
    assert "secret_ciphertext" not in raw
    items = json.loads(raw)
    assert len(items) == 1 and items[0]["service"] == "fcc_cores"

    # Rotate via the API.
    await c.post(
        f"/clients/{cid}/credentials",
        headers=h,
        json={"service": "fcc_cores", "secret": "ROTATED"},
    )
    items = (await c.get(f"/clients/{cid}/credentials", headers=h)).json()
    assert len(items) == 1  # upsert, not duplicate

    # Operator isolation.
    reg2 = await c.post(
        "/auth/register",
        json={"email": "b@acme.com", "password": "supersecret", "name": "B"},
    )
    hb = {"Authorization": f"Bearer {reg2.json()['access_token']}"}
    assert (await c.get(f"/clients/{cid}/credentials", headers=hb)).status_code == 404

    # Delete.
    assert (
        await c.delete(f"/clients/{cid}/credentials/{cred_id}", headers=h)
    ).status_code == 204
    assert (await c.get(f"/clients/{cid}/credentials", headers=h)).json() == []


def _usage():
    return SimpleNamespace(
        input_tokens=10,
        output_tokens=5,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )


class _FakeCredCheck:
    def __init__(self):
        self.messages = self
        self._n = 0

    async def create(self, **_kw):
        self._n += 1
        if self._n == 1:
            return SimpleNamespace(
                stop_reason="tool_use",
                usage=_usage(),
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        id="t1",
                        name="check_client_credentials",
                        input={},
                    )
                ],
            )
        return SimpleNamespace(
            stop_reason="end_turn",
            usage=_usage(),
            content=[SimpleNamespace(type="text", text="done"),],
        )


async def test_agent_tool_lists_creds_audited_no_secret(db: AsyncSession):
    cid = uuid.uuid4()
    await store_credential(
        db, client_id=cid, service="fcc_cores", secret="TOPSECRET"
    )
    await db.commit()

    result = await run_agent(
        COMPLIANCE_AGENT,
        client=_FakeCredCheck(),
        db=db,
        task_id=uuid.uuid4(),
        instruction="What credentials are on file?",
        client_id=cid,
    )
    assert result.text == "done"

    audits = (await db.scalars(select(AuditLog))).all()
    actions_by_actor = {(a.action, a.actor) for a in audits}
    assert ("credential.listed", "compliance") in actions_by_actor

    # Secret must not appear anywhere persisted by the agent run.
    for a in audits:
        blob = json.dumps({"before": a.before, "after": a.after})
        assert "TOPSECRET" not in blob


# --- URL field + edit endpoint (v1.3.3) ------------------------------------


async def test_url_stored_and_returned_via_api(db: AsyncSession):
    """Adding a credential with a URL — server-side encrypts the
    secret, stores the URL as plaintext (non-secret metadata), and
    returns it on subsequent reads."""
    cid = uuid.uuid4()
    cred = await store_credential(
        db,
        client_id=cid,
        service="fcc_cores",
        secret="hunter2",
        username="amber@amano.example",
        url="https://apps.fcc.gov/cores/userLogin.do",
    )
    assert cred.url == "https://apps.fcc.gov/cores/userLogin.do"
    # URL was not encrypted.
    fresh = await db.scalar(select(Credential).where(Credential.id == cred.id))
    assert fresh.url == "https://apps.fcc.gov/cores/userLogin.do"


async def test_known_service_auto_fills_url(db: AsyncSession):
    """Routes layer: if the operator doesn't supply a URL but the
    service is in the well-known table, the URL gets populated. We
    test this through the API since the auto-fill lives in the route."""
    from app.api.routes.credentials import _default_url_for

    assert _default_url_for("fcc_cores").startswith("https://apps.fcc.gov")
    assert _default_url_for("FCC CORES") is not None  # normalized
    assert _default_url_for("twilio").startswith("https://console.twilio.com")
    # Unknown service → None, operator types their own.
    assert _default_url_for("some_random_carrier") is None


@pytest_asyncio.fixture
async def http():
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


async def test_patch_edits_url_username_without_rotating_secret(http):
    """The operator can edit URL / username / expiration on an
    existing credential WITHOUT touching the secret. The original
    secret stays decryptable; only the metadata changes."""
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op@example.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]

    # Initial create with no URL — known-service auto-fill kicks in.
    r = await c.post(
        f"/clients/{cid}/credentials",
        headers=h,
        json={
            "service": "fcc_cores",
            "username": "amber@amano.example",
            "secret": "original-password",
        },
    )
    assert r.status_code == 201
    body = r.json()
    cred_id = body["id"]
    assert body["url"] == "https://apps.fcc.gov/cores/userLogin.do"
    assert body["username"] == "amber@amano.example"

    # Patch only the URL — username + secret untouched.
    r = await c.patch(
        f"/clients/{cid}/credentials/{cred_id}",
        headers=h,
        json={"url": "https://apps.fcc.gov/cores/v2/login"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["url"] == "https://apps.fcc.gov/cores/v2/login"
    assert body["username"] == "amber@amano.example"  # untouched
    # Original secret still decryptable (no rotation).
    async with maker() as s:
        row = await s.scalar(select(Credential).where(Credential.id == uuid.UUID(cred_id)))
        assert decrypt(row.secret_ciphertext) == "original-password"


async def test_patch_can_rotate_secret(http):
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op@example.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    r = await c.post(
        f"/clients/{cid}/credentials",
        headers=h,
        json={"service": "carrier_x", "secret": "old-secret"},
    )
    cred_id = r.json()["id"]

    r = await c.patch(
        f"/clients/{cid}/credentials/{cred_id}",
        headers=h,
        json={"new_secret": "new-secret"},
    )
    assert r.status_code == 200
    async with maker() as s:
        row = await s.scalar(select(Credential).where(Credential.id == uuid.UUID(cred_id)))
        assert decrypt(row.secret_ciphertext) == "new-secret"


async def test_patch_never_leaks_secret_in_audit(http):
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op@example.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]
    r = await c.post(
        f"/clients/{cid}/credentials",
        headers=h,
        json={"service": "carrier_x", "secret": "INITIAL-CANARY"},
    )
    cred_id = r.json()["id"]
    await c.patch(
        f"/clients/{cid}/credentials/{cred_id}",
        headers=h,
        json={
            "url": "https://carrierx.example/login",
            "new_secret": "ROTATED-CANARY",
        },
    )
    async with maker() as s:
        audits = (await s.scalars(select(AuditLog))).all()
    for a in audits:
        blob = json.dumps({"before": a.before, "after": a.after})
        assert "INITIAL-CANARY" not in blob
        assert "ROTATED-CANARY" not in blob


async def test_patch_other_operator_cannot_edit(http):
    c, _ = http
    reg_a = await c.post(
        "/auth/register",
        json={"email": "a@example.com", "password": "supersecret", "name": "A"},
    )
    ha = {"Authorization": f"Bearer {reg_a.json()['access_token']}"}
    cid = (await c.post("/clients", headers=ha, json={"name": "Acme"})).json()["id"]
    r = await c.post(
        f"/clients/{cid}/credentials",
        headers=ha,
        json={"service": "carrier_x", "secret": "secret"},
    )
    cred_id = r.json()["id"]
    reg_b = await c.post(
        "/auth/register",
        json={"email": "b@example.com", "password": "supersecret", "name": "B"},
    )
    hb = {"Authorization": f"Bearer {reg_b.json()['access_token']}"}
    r = await c.patch(
        f"/clients/{cid}/credentials/{cred_id}",
        headers=hb,
        json={"url": "https://evil.example"},
    )
    assert r.status_code == 404
