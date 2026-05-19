"""Settings: an operator can update their display name and change their
password. Password change verifies the current secret, blocks reusing
the same one, and audits the event without storing the secret itself.
"""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import AuditLog


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


async def _register(c: AsyncClient, **kw) -> dict:
    body = {
        "email": "op@example.com",
        "password": "originalpass",
        "name": "Op",
        **kw,
    }
    r = await c.post("/auth/register", json=body)
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_profile_rename(ctx):
    c, _ = ctx
    h = await _register(c)
    r = await c.put("/auth/me", headers=h, json={"name": "Operator Alpha"})
    assert r.status_code == 200
    assert r.json()["name"] == "Operator Alpha"
    # Persists across reads.
    assert (await c.get("/auth/me", headers=h)).json()["name"] == "Operator Alpha"


async def test_change_password_happy_and_failures(ctx):
    c, maker = ctx
    h = await _register(c)

    # Wrong current password → 400.
    r = await c.post(
        "/auth/change-password",
        headers=h,
        json={"current_password": "wrong", "new_password": "newpass123"},
    )
    assert r.status_code == 400

    # Same as current → 400.
    r = await c.post(
        "/auth/change-password",
        headers=h,
        json={
            "current_password": "originalpass",
            "new_password": "originalpass",
        },
    )
    assert r.status_code == 400

    # Happy path.
    r = await c.post(
        "/auth/change-password",
        headers=h,
        json={
            "current_password": "originalpass",
            "new_password": "newpass123",
        },
    )
    assert r.status_code == 204

    # Old password no longer works; new one does.
    assert (
        await c.post(
            "/auth/login",
            json={"email": "op@example.com", "password": "originalpass"},
        )
    ).status_code == 401
    assert (
        await c.post(
            "/auth/login",
            json={"email": "op@example.com", "password": "newpass123"},
        )
    ).status_code == 200

    # Audit entry was recorded — but never the secret itself.
    async with maker() as s:
        entries = (await s.scalars(select(AuditLog))).all()
    actions = [e.action for e in entries]
    assert "account.password_changed" in actions
    assert not any(
        ("newpass123" in (e.action or "")) or ("originalpass" in (e.action or ""))
        for e in entries
    )
