"""GUI-editable platform config: PUT /system/config writes encrypted
DB rows for secrets, GET /system/status reports readiness without
ever returning the secret values themselves. The Anthropic-client
dependency reads the DB-stored key first so the GUI's just-saved key
takes effect on the next agent call (no API restart needed).
"""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import platform_config
from app.config import settings
from app.db import Base, get_db
from app.llm import get_anthropic_client
from app.main import app


@pytest_asyncio.fixture
async def client():
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
    # Clear env so DB is the only source of the key.
    saved = settings.anthropic_api_key
    settings.anthropic_api_key = ""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    settings.anthropic_api_key = saved
    app.dependency_overrides.clear()
    await engine.dispose()


async def _auth(c: AsyncClient) -> dict:
    r = await c.post(
        "/auth/register",
        json={"email": "op@example.com", "password": "supersecret", "name": "Op"},
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_initially_unset_then_save_via_gui(client: AsyncClient):
    h = await _auth(client)
    # Status: anthropic unset, smtp unconfigured.
    r = await client.get("/system/status", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["anthropic"] is False
    assert body["smtp_configured"] is False

    # Save the key via the GUI endpoint.
    r = await client.put(
        "/system/config",
        headers=h,
        json={
            "anthropic_api_key": "sk-ant-CANARY",
            "smtp_host": "smtp.example.com",
            "smtp_from": "ops@switchboard.test",
            "smtp_password": "smtp-pw-CANARY",
        },
    )
    assert r.status_code == 204, r.text

    # Status flips to ready — but never echoes the secrets.
    r = await client.get("/system/status", headers=h)
    body = r.json()
    assert body["anthropic"] is True
    assert body["smtp_configured"] is True
    assert body["smtp_host"] == "smtp.example.com"
    assert body["smtp_from"] == "ops@switchboard.test"
    # Critical: neither secret leaks.
    assert "sk-ant-CANARY" not in r.text
    assert "smtp-pw-CANARY" not in r.text


async def test_anthropic_dependency_reads_db_key(client: AsyncClient):
    """After the operator saves the key via the GUI, the next
    get_anthropic_client call must use the DB key — no API restart."""
    h = await _auth(client)
    # No key anywhere: agent run would 503. We exercise the dep directly.
    from app.db import get_db as _gd

    db_iter = app.dependency_overrides[_gd]()
    db = await db_iter.__anext__()
    try:
        from fastapi import HTTPException
        try:
            await get_anthropic_client(db)
        except HTTPException as e:
            assert e.status_code == 503
        else:
            raise AssertionError("expected 503 when key is unset")
    finally:
        try:
            await db_iter.__anext__()
        except StopAsyncIteration:
            pass

    # Save key via the GUI; the next dep call should NOT 503.
    r = await client.put(
        "/system/config", headers=h, json={"anthropic_api_key": "sk-ant-real"}
    )
    assert r.status_code == 204

    db_iter = app.dependency_overrides[_gd]()
    db = await db_iter.__anext__()
    try:
        c = await get_anthropic_client(db)
        # AsyncAnthropic instance — assert by class name to avoid
        # importing the SDK in this test.
        assert c.__class__.__name__ == "AsyncAnthropic"
    finally:
        try:
            await db_iter.__anext__()
        except StopAsyncIteration:
            pass


async def test_clearing_a_secret_falls_back_to_env(client: AsyncClient):
    h = await _auth(client)
    # Save a key, then clear it, then verify env fallback.
    await client.put(
        "/system/config", headers=h, json={"anthropic_api_key": "sk-ant-DB"}
    )
    settings.anthropic_api_key = "sk-ant-ENV-FALLBACK"
    try:
        # Clear via empty string.
        r = await client.put(
            "/system/config", headers=h, json={"anthropic_api_key": ""}
        )
        assert r.status_code == 204
        r = await client.get("/system/status", headers=h)
        assert r.json()["anthropic"] is True  # env still provides it
    finally:
        settings.anthropic_api_key = ""


async def test_unknown_key_in_payload_is_ignored(client: AsyncClient):
    """Extra fields in PUT are silently dropped by Pydantic — only the
    allowed keys reach platform_config."""
    h = await _auth(client)
    r = await client.put(
        "/system/config",
        headers=h,
        json={"anthropic_api_key": "sk-1", "evil": "value"},
    )
    assert r.status_code == 204
