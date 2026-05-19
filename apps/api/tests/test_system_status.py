"""/system/status reveals integration readiness — never the secret
values themselves. Auth-gated; anonymous callers get 401."""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
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
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


async def test_status_requires_auth(client: AsyncClient):
    r = await client.get("/system/status")
    assert r.status_code in (401, 403)


async def test_status_reflects_settings_without_leaking_secrets(client: AsyncClient):
    saved_key = settings.anthropic_api_key
    saved_smtp_pw = settings.smtp_password
    settings.anthropic_api_key = "sk-ant-LEAK-CANARY"
    settings.smtp_host = "smtp.example.com"
    settings.smtp_password = "LEAK-CANARY-PASSWORD"
    settings.smtp_from = "ops@switchboard.example.com"
    try:
        r = await client.post(
            "/auth/register",
            json={"email": "op@example.com", "password": "supersecret", "name": "Op"},
        )
        tok = r.json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        r = await client.get("/system/status", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["anthropic"] is True
        assert body["smtp"] is True
        assert body["smtp_host"] == "smtp.example.com"
        # Critical: secrets must never appear in the response.
        assert "LEAK-CANARY" not in r.text
    finally:
        settings.anthropic_api_key = saved_key
        settings.smtp_password = saved_smtp_pw
        settings.smtp_host = ""
        settings.smtp_from = ""
