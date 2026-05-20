"""GUI-managed agents: an operator creates / edits / disables agents
without touching code. Tools stay code-defined (tier safety property)
but agents can be composed from the catalog and assigned a custom
system prompt and model. A custom agent overrides the built-in of the
same name at run time; disabling falls back to the built-in.
"""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.llm import get_anthropic_client
from app.main import app

# Minimal fake to satisfy the dep when tests do an agent run.
class _Fake:
    pass


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
    app.dependency_overrides[get_anthropic_client] = lambda: _Fake()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


async def _auth(c: AsyncClient, email: str = "op@example.com") -> dict:
    r = await c.post(
        "/auth/register",
        json={"email": email, "password": "supersecret", "name": "Op"},
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_tool_catalog_lists_code_defined_tools_with_tiers(client: AsyncClient):
    h = await _auth(client)
    r = await client.get("/agents/tools", headers=h)
    assert r.status_code == 200
    tools = r.json()
    names = {t["name"] for t in tools}
    # Spot-check a few we know exist.
    assert "lookup_fcc_requirement" in names
    assert "queue_filing_submission" in names
    assert "request_portal_action" in names
    # Each tool has a tier field — never empty.
    assert all(t["tier"] for t in tools)


async def test_list_includes_builtins_plus_custom(client: AsyncClient):
    h = await _auth(client)
    r = await client.get("/agents", headers=h)
    by_name = {a["name"]: a for a in r.json()}
    # Built-ins are present and marked.
    for n in ("pm", "compliance", "document"):
        assert n in by_name and by_name[n]["is_builtin"] is True


async def test_create_update_disable_delete(client: AsyncClient):
    h = await _auth(client)
    r = await client.post(
        "/agents",
        headers=h,
        json={
            "name": "carrier",
            "description": "Wholesale carrier interconnection",
            "system_prompt": "You are the Carrier Agent. Compose carrier paperwork.",
            "tool_names": ["lookup_fcc_requirement", "request_portal_action"],
            "model": "claude-haiku-4-5",
        },
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["is_builtin"] is False
    assert created["tool_names"] == [
        "lookup_fcc_requirement",
        "request_portal_action",
    ]
    aid = created["id"]

    # Name collision per-owner → 409.
    r = await client.post(
        "/agents",
        headers=h,
        json={
            "name": "carrier",
            "system_prompt": "Another carrier agent attempting to collide on name.",
            "tool_names": [],
        },
    )
    assert r.status_code == 409

    # Update: change system prompt + disable.
    r = await client.put(
        f"/agents/{aid}",
        headers=h,
        json={
            "system_prompt": "You are the Carrier Agent (v2). Be terse.",
            "enabled": False,
        },
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is False

    # Delete.
    r = await client.delete(f"/agents/{aid}", headers=h)
    assert r.status_code == 204
    r = await client.get("/agents", headers=h)
    assert not any(a["id"] == aid for a in r.json())


async def test_unknown_tool_name_is_rejected(client: AsyncClient):
    h = await _auth(client)
    r = await client.post(
        "/agents",
        headers=h,
        json={
            "name": "broken",
            "system_prompt": "I reference a nonexistent tool.",
            "tool_names": ["does_not_exist"],
        },
    )
    assert r.status_code == 400
    assert "does_not_exist" in r.json()["detail"]


async def test_other_operator_cannot_see_or_edit(client: AsyncClient):
    ha = await _auth(client, "a@example.com")
    r = await client.post(
        "/agents",
        headers=ha,
        json={
            "name": "alpha",
            "system_prompt": "Operator A's private agent.",
            "tool_names": [],
        },
    )
    aid = r.json()["id"]

    hb = await _auth(client, "b@example.com")
    # B doesn't see A's custom agent (still sees built-ins).
    r = await client.get("/agents", headers=hb)
    assert all(a["name"] != "alpha" for a in r.json())
    # B can't update or delete A's agent.
    r = await client.put(f"/agents/{aid}", headers=hb, json={"description": "evil"})
    assert r.status_code == 404
    r = await client.delete(f"/agents/{aid}", headers=hb)
    assert r.status_code == 404
