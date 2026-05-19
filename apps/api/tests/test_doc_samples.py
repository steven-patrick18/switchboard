"""Every mandated document has a downloadable spec/template the operator
sends the client. Scan requirement flows from the shared catalog.
"""

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.doc_samples import all_sample_keys, get_sample
from app.intake import catalog_keys
from app.main import app


def test_every_catalog_doc_has_a_sample():
    assert set(all_sample_keys()) == set(catalog_keys())
    for key in catalog_keys():
        s = get_sample(key)
        assert s is not None
        filename, text = s
        assert filename == f"{key}-requirements.txt"
        assert "SWITCHBOARD — DOCUMENT REQUEST" in text
    assert get_sample("nope") is None


def test_scan_requirement_in_text():
    # needs_scan True
    _, idtext = get_sample("officer_government_id")
    assert "SCAN" in idtext and "REQUIRED" in idtext
    # needs_scan False
    _, eintext = get_sample("ein_letter")
    assert "PDF or scan is acceptable" in eintext


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
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


async def test_download_sample_endpoint(http):
    c = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op@acme.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = (await c.post("/clients", headers=h, json={"name": "Acme"})).json()["id"]

    r = await c.get(
        f"/clients/{cid}/documents/officer_government_id/sample", headers=h
    )
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert "officer_government_id-requirements.txt" in r.headers[
        "content-disposition"
    ]
    assert "Officer government-issued ID" in r.text
    assert "SCAN" in r.text

    # Unknown document key.
    assert (
        await c.get(f"/clients/{cid}/documents/bogus/sample", headers=h)
    ).status_code == 404

    # Operator-scoped.
    reg2 = await c.post(
        "/auth/register",
        json={"email": "b@acme.com", "password": "supersecret", "name": "B"},
    )
    hb = {"Authorization": f"Bearer {reg2.json()['access_token']}"}
    assert (
        await c.get(
            f"/clients/{cid}/documents/ein_letter/sample", headers=hb
        )
    ).status_code == 404
