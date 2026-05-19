"""End-to-end smoke test for the Phase 1 API.

Exercises auth + client-workspace isolation against the running app via an
in-process ASGI transport (no server needed).

Usage (from apps/api, with deps installed and a migrated DB):

    # Postgres (default):
    python -m alembic upgrade head
    python scripts/smoke.py

    # Or against a throwaway SQLite DB:
    set DATABASE_URL=sqlite+aiosqlite:///./smoke.db   # Windows
    python -m alembic upgrade head
    python scripts/smoke.py

Prints "SMOKE PASS" on success; raises AssertionError otherwise.
"""

import asyncio
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402


async def main() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/health")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ok"

        r = await c.post(
            "/auth/register",
            json={"email": "a@example.com", "password": "supersecret", "name": "Op A"},
        )
        assert r.status_code == 201, r.text
        r = await c.post(
            "/auth/login", json={"email": "a@example.com", "password": "supersecret"}
        )
        assert r.status_code == 200, r.text
        tok_a = r.json()["access_token"]
        ha = {"Authorization": f"Bearer {tok_a}"}

        r = await c.get("/auth/me", headers=ha)
        assert r.status_code == 200 and r.json()["email"] == "a@example.com", r.text

        r = await c.post(
            "/auth/login", json={"email": "a@example.com", "password": "wrong"}
        )
        assert r.status_code == 401, r.text

        r = await c.post(
            "/clients", headers=ha, json={"name": "Acme VoIP", "state": "TX"}
        )
        assert r.status_code == 201, r.text
        assert r.json()["stage"] == "intake"
        r = await c.get("/clients", headers=ha)
        assert r.status_code == 200 and len(r.json()) == 1, r.text

        # Workspace isolation: operator B sees none of operator A's clients.
        r = await c.post(
            "/auth/register",
            json={"email": "b@example.com", "password": "supersecret", "name": "Op B"},
        )
        tok_b = r.json()["access_token"]
        hb = {"Authorization": f"Bearer {tok_b}"}
        r = await c.get("/clients", headers=hb)
        assert r.status_code == 200 and r.json() == [], r.text

        r = await c.get("/clients")
        assert r.status_code in (401, 403), r.text

    print("SMOKE PASS")


if __name__ == "__main__":
    asyncio.run(main())
