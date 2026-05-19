"""One emailable, client-ready PDF document request pack — cover +
checklist + each mandated doc's spec, tailored to the client's intake.
Content is verified by extracting the PDF text with pdfplumber.
"""

import io
import uuid
from types import SimpleNamespace

import pdfplumber
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.doc_samples import build_request_pack_pdf
from app.intake import resolve_required_documents
from app.main import app

_FULL = {
    "legal_name": "Acme VoIP LLC",
    "entity_type": "LLC",
    "formation_state": "DE",
    "ein": "99-1234567",
    "principal_address": {"street": "1 Main"},
    "officer_name": "Jane Roe",
    "officer_title": "CEO",
    "officer_email": "jane@acme.example",
    "primary_contact_name": "Jane Roe",
    "primary_contact_email": "jane@acme.example",
    "primary_contact_phone": "+15125550100",
    "target_states": ["TX"],
    "estimated_monthly_revenue": 25000,
}


def _pdf_text(data: bytes) -> str:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


def test_pdf_pack_assembly_base_and_tailored():
    fn, data = build_request_pack_pdf(
        "Acme VoIP LLC", resolve_required_documents(None)
    )
    assert fn == "acme-voip-llc-document-request.pdf"
    assert data[:5] == b"%PDF-"
    text = _pdf_text(data)
    assert "Document Request" in text and "Acme VoIP LLC" in text
    assert "Checklist" in text
    assert "Officer government-issued ID" in text
    assert "Notarized state CPCN packet" not in text  # no states in base

    tailored = SimpleNamespace(intends_international=True, target_states=["TX"])
    _, d2 = build_request_pack_pdf("Acme", resolve_required_documents(tailored))
    t2 = _pdf_text(d2)
    assert "Notarized state CPCN packet" in t2
    assert "FCC Section 214 international support" in t2


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


async def test_request_pack_endpoint_pdf(http):
    c = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op@acme.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = (await c.post("/clients", headers=h, json={"name": "Acme VoIP"})).json()[
        "id"
    ]

    r = await c.get(f"/clients/{cid}/documents/request-pack", headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert "attachment" in r.headers["content-disposition"]
    assert ".pdf" in r.headers["content-disposition"]
    assert r.content[:5] == b"%PDF-"
    text = _pdf_text(r.content)
    assert "Acme VoIP" in text and "Checklist" in text
    assert "Notarized state CPCN packet" not in text  # no intake yet

    await c.put(f"/clients/{cid}/intake", headers=h, json=_FULL)
    r = await c.get(f"/clients/{cid}/documents/request-pack", headers=h)
    assert "Notarized state CPCN packet" in _pdf_text(r.content)

    reg2 = await c.post(
        "/auth/register",
        json={"email": "b@acme.com", "password": "supersecret", "name": "B"},
    )
    hb = {"Authorization": f"Bearer {reg2.json()['access_token']}"}
    assert (
        await c.get(f"/clients/{cid}/documents/request-pack", headers=hb)
    ).status_code == 404
