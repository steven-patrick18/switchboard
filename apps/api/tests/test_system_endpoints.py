"""/system/update, /system/certificate, /health/detailed.

These are the production-operations endpoints the Settings GUI calls:
operator checks if there's a new commit on main, requests an update
(writes a sentinel the host's cron polls), reads the HTTPS cert info,
and runs a per-subsystem self-test. Everything is auth-gated.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.main import app


@pytest_asyncio.fixture
async def client(tmp_path: Path):
    # Re-point storage + sentinel file into the test's tmp dir so the
    # /health/detailed and /system/update tests have writable spots.
    saved_docs = settings.documents_dir
    saved_sentinel = settings.update_request_file
    settings.documents_dir = str(tmp_path / "docs")
    settings.update_request_file = str(tmp_path / "run" / "update-requested")

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
    settings.documents_dir = saved_docs
    settings.update_request_file = saved_sentinel
    await engine.dispose()


async def _auth(c: AsyncClient) -> dict:
    r = await c.post(
        "/auth/register",
        json={"email": "op@example.com", "password": "supersecret", "name": "Op"},
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---------- /system/update ------------------------------------------------


def _mock_github(payload: dict | None, error: str | None = None):
    """Returns an async callable matching _fetch_latest_main_commit's
    signature, so we can patch it without monkey-patching httpx
    (which the test client itself depends on)."""

    async def _fake(repo: str):  # noqa: ARG001
        return payload, error

    return _fake


async def test_update_status_compares_current_vs_github(client: AsyncClient):
    h = await _auth(client)
    settings.railway_git_commit_sha = "abcdef0123456789"
    try:
        payload = {
            "sha": "fedcba9876543210",
            "commit": {
                "message": "Bump the thing",
                "committer": {"date": "2026-05-21T10:00:00Z"},
                "author": {"name": "Ops"},
            },
            "html_url": "https://github.com/x/y/commit/fedcba98",
        }
        with patch("app.main._fetch_latest_main_commit", _mock_github(payload)):
            r = await client.get("/system/update", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["current_commit"] == "abcdef012345"
        assert body["latest_available"]["sha"] == "fedcba987654"
        assert body["latest_available"]["message"] == "Bump the thing"
        assert body["up_to_date"] is False
        assert body["update_pending"] is False
    finally:
        settings.railway_git_commit_sha = ""


async def test_update_status_reports_up_to_date(client: AsyncClient):
    h = await _auth(client)
    settings.railway_git_commit_sha = "abcdef0123456789"
    try:
        payload = {
            "sha": "abcdef0123456789",
            "commit": {
                "message": "Already there",
                "committer": {"date": "2026-05-21T10:00:00Z"},
                "author": {"name": "Ops"},
            },
            "html_url": "https://github.com/x/y/commit/abc",
        }
        with patch("app.main._fetch_latest_main_commit", _mock_github(payload)):
            r = await client.get("/system/update", headers=h)
        assert r.json()["up_to_date"] is True
    finally:
        settings.railway_git_commit_sha = ""


async def test_update_status_handles_github_unreachable(client: AsyncClient):
    h = await _auth(client)
    with patch(
        "app.main._fetch_latest_main_commit",
        _mock_github(None, "GitHub API call failed: RuntimeError"),
    ):
        r = await client.get("/system/update", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["latest_available"] is None
    assert "GitHub" in body["check_error"]


async def test_request_update_writes_sentinel(client: AsyncClient):
    h = await _auth(client)
    sentinel = Path(settings.update_request_file)
    assert not sentinel.exists()

    r = await client.post("/system/update/request", headers=h)
    assert r.status_code == 202
    assert sentinel.exists()
    meta = json.loads(sentinel.read_text())
    assert meta["requested_by"] == "op@example.com"
    assert "requested_at" in meta

    # /system/update now reports update_pending=True with meta.
    with patch(
        "app.main._fetch_latest_main_commit",
        _mock_github(None, "offline"),
    ):
        r = await client.get("/system/update", headers=h)
    body = r.json()
    assert body["update_pending"] is True
    assert body["update_pending_meta"]["requested_by"] == "op@example.com"

    # Cancel removes it.
    r = await client.delete("/system/update/request", headers=h)
    assert r.status_code == 204
    assert not sentinel.exists()


async def test_update_endpoints_require_auth(client: AsyncClient):
    assert (await client.get("/system/update")).status_code in (401, 403)
    assert (
        await client.post("/system/update/request")
    ).status_code in (401, 403)


# ---------- /system/certificate ------------------------------------------


async def test_certificate_unavailable_when_no_https_base(client: AsyncClient):
    h = await _auth(client)
    # default app_base_url is empty in test
    r = await client.get("/system/certificate", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert "HTTPS" in body["reason"]


async def test_certificate_reports_connection_failure_cleanly(
    client: AsyncClient,
):
    h = await _auth(client)
    saved = settings.app_base_url
    settings.app_base_url = "https://definitely-not-real-12345.example.invalid"
    try:
        r = await client.get("/system/certificate", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is False
        assert "Could not reach" in body["reason"]
    finally:
        settings.app_base_url = saved


# ---------- /health/detailed ---------------------------------------------


async def test_detailed_health_runs_all_checks(client: AsyncClient):
    h = await _auth(client)
    r = await client.get("/health/detailed", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert "checks" in body
    expected = {
        "database",
        "document_storage",
        "anthropic_key",
        "smtp",
        "update_channel",
    }
    assert expected <= set(body["checks"].keys())
    # DB + documents + update_channel should all be ok in tmp-path land.
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["document_storage"]["ok"] is True
    assert body["checks"]["update_channel"]["ok"] is True


async def test_detailed_health_requires_auth(client: AsyncClient):
    assert (await client.get("/health/detailed")).status_code in (401, 403)
