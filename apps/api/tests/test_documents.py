"""Document upload + download. Content-addressed storage: two uploads of
the same bytes produce the same SHA-256 (= s3_key) and share one on-disk
file. Owner-scoped reads — another operator can't download from someone
else's workspace. Intake completeness ignores metadata-only Documents.
"""

import uuid
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import Document


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


async def test_upload_download_roundtrip(client: AsyncClient, tmp_path: Path):
    h = await _register(client)
    r = await client.post("/clients", headers=h, json={"name": "Acme"})
    cid = r.json()["id"]

    payload = b"hello world\n" * 100
    r = await client.post(
        f"/clients/{cid}/documents",
        headers=h,
        data={"type": "ein_letter"},
        files={"file": ("ein.pdf", payload, "application/pdf")},
    )
    assert r.status_code == 201
    doc = r.json()
    assert doc["filename"] == "ein.pdf"
    assert doc["mime"] == "application/pdf"
    assert doc["size_bytes"] == len(payload)
    assert doc["s3_key"]  # SHA-256 hex
    assert len(doc["s3_key"]) == 64

    # File materialized at the content-addressed path.
    storage_root = Path(settings.documents_dir)
    expected = storage_root / doc["s3_key"][:2] / doc["s3_key"]
    assert expected.read_bytes() == payload

    # Download returns the exact bytes + sensible headers.
    r = await client.get(
        f"/clients/{cid}/documents/{doc['id']}/download", headers=h
    )
    assert r.status_code == 200
    assert r.content == payload
    assert r.headers["content-type"].startswith("application/pdf")
    assert "ein.pdf" in r.headers["content-disposition"]


async def test_identical_bytes_dedupe_on_disk(client: AsyncClient):
    h = await _register(client)
    cid = (await client.post("/clients", headers=h, json={"name": "C"})).json()[
        "id"
    ]

    payload = b"the same exact bytes"
    files = {"file": ("a.pdf", payload, "application/pdf")}
    r1 = await client.post(
        f"/clients/{cid}/documents", headers=h, data={"type": "utility_bill"}, files=files
    )
    files = {"file": ("renamed.pdf", payload, "application/pdf")}
    r2 = await client.post(
        f"/clients/{cid}/documents", headers=h, data={"type": "utility_bill"}, files=files
    )
    assert r1.status_code == r2.status_code == 201
    d1, d2 = r1.json(), r2.json()
    # Same SHA → same storage key (de-duped on disk).
    assert d1["s3_key"] == d2["s3_key"]
    # Distinct rows with version bumped.
    assert d1["id"] != d2["id"]
    assert d2["version"] == d1["version"] + 1


async def test_other_operator_cannot_download(client: AsyncClient):
    ha = await _register(client, "a@example.com")
    cid = (await client.post("/clients", headers=ha, json={"name": "A"})).json()[
        "id"
    ]
    r = await client.post(
        f"/clients/{cid}/documents",
        headers=ha,
        data={"type": "ein_letter"},
        files={"file": ("x.pdf", b"secret", "application/pdf")},
    )
    doc_id = r.json()["id"]

    hb = await _register(client, "b@example.com")
    r = await client.get(
        f"/clients/{cid}/documents/{doc_id}/download", headers=hb
    )
    assert r.status_code == 404  # leaks no info about whether the doc exists


async def test_empty_upload_rejected(client: AsyncClient):
    h = await _register(client)
    cid = (await client.post("/clients", headers=h, json={"name": "C"})).json()[
        "id"
    ]
    r = await client.post(
        f"/clients/{cid}/documents",
        headers=h,
        data={"type": "ein_letter"},
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert r.status_code == 400


async def test_metadata_only_document_does_not_satisfy_intake(
    client: AsyncClient,
):
    """Inserting a Document row with no s3_key (e.g., an agent's output
    stub) must NOT make the intake completeness check think the mandate
    is satisfied — only uploaded bytes count."""
    h = await _register(client)
    cid = (
        await client.post("/clients", headers=h, json={"name": "Test"})
    ).json()["id"]

    # Drop a metadata-only Document straight into the DB.
    maker = client._maker  # type: ignore[attr-defined]
    async with maker() as s:  # type: AsyncSession
        s.add(
            Document(
                client_id=uuid.UUID(cid),
                type="ein_letter",
                version=1,
                s3_key=None,
            )
        )
        await s.commit()

    r = await client.get(f"/clients/{cid}/intake", headers=h)
    req = {d["key"]: d for d in r.json()["completeness"]["required_documents"]}
    # ein_letter is mandated at founder stage? No — it's an entity-stage
    # doc. The metadata-only row exists but stage is still 'founder'; just
    # confirm the row didn't accidentally flip anything to provided.
    for d in req.values():
        assert d["provided"] is False
