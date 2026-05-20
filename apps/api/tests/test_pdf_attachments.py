"""Approved filings should produce actual mailable PDFs that get
attached to the outbound email — not just a Document Hub stub and a
text-only body. This locks in:
  1. The per-form PDF builders return valid PDF bytes.
  2. The filing executor persists the PDFs to content-addressed
     storage and records them on `Approval.email_packet.attachments`.
  3. The /approvals/{id}/send-email endpoint loads the attachment
     bytes and includes them in the SMTP message (we mock the actual
     send and inspect the call).
"""

import uuid
from unittest.mock import patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.execution import execute_approval
from app.main import app
from app.models import Approval, ClientIntake, Document, Project, Task
from app.models.approval import DECISION_APPROVED, DECISION_PENDING, TIER_SIGN_PAY
from app.pdf_builders import (
    build_generic_filing_pdf,
    build_letter_of_agency_pdf,
    build_neca_ocn_2_pdf,
)
from app.vault import store_credential


# --- Pure PDF-builder unit tests --------------------------------------


def test_neca_ocn_2_pdf_returns_valid_pdf_bytes():
    payload = {
        "form": "NECA-OCN-2",
        "applicant_legal_name": "Amano Telecom LLC",
        "ein": "39-2196239",
        "frn": "0037045218",
        "entity_type": "LLC",
        "formation_state": "WY",
        "principal_business_address": {
            "street": "30 N Gould Ste 100",
            "city": "Sheridan",
            "state": "WY",
            "zip": "82801",
        },
        "company_type_requested": "CAP/CLEC",
        "service_area_states": ["WY"],
        "intends_international": False,
        "authorized_officer": {
            "name": "AMBER SIDNEY HUNT",
            "title": "Single Owner",
            "email": "amber@amanotelecom.com",
        },
        "primary_operational_contact": {
            "name": "Steven Patrick",
            "email": "noc@amanotelecom.com",
            "phone": "2027538811",
        },
    }
    data = build_neca_ocn_2_pdf(payload, legal_name="Amano Telecom LLC")
    assert data.startswith(b"%PDF-"), "Output is not a valid PDF"
    assert len(data) > 1000, "PDF is suspiciously small"


def test_letter_of_agency_pdf_returns_valid_pdf_bytes():
    payload = {
        "letter_of_agency": {
            "grantor": "Amano Telecom LLC",
            "grantee": "Switchboard (filing agent)",
            "scope": [
                "Prepare and submit NECA-OCN-2 on behalf of the Grantor.",
                "Receive NECA notices.",
            ],
            "signatory_name": "AMBER SIDNEY HUNT",
            "signatory_title": "Single Owner",
            "signatory_email": "amber@amanotelecom.com",
            "effective_date": "2026-05-20",
            "expiration": "Until revoked in writing",
        },
    }
    data = build_letter_of_agency_pdf(payload, legal_name="Amano Telecom LLC")
    assert data.startswith(b"%PDF-")
    assert len(data) > 1000


def test_generic_pdf_handles_nested_payload():
    payload = {"form": "FCC 499-A", "frn": "0037045218", "nested": {"a": 1}}
    data = build_generic_filing_pdf(
        "FCC 499-A", payload, legal_name="Amano Telecom LLC"
    )
    assert data.startswith(b"%PDF-")


# --- Executor integration: PDFs persisted + linked --------------------


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


async def _seed_amano(db: AsyncSession) -> uuid.UUID:
    from app.models import Client  # noqa: PLC0415
    from app.models.user import User  # noqa: PLC0415

    u = User(email="op@a.com", hashed_password="x", name="Op")
    db.add(u)
    await db.flush()
    c = Client(owner_id=u.id, name="Amano Telecom LLC")
    db.add(c)
    await db.flush()
    db.add(
        ClientIntake(
            client_id=c.id,
            legal_name="Amano Telecom LLC",
            ein="39-2196239",
            officer_email="amber@amanotelecom.com",
        )
    )
    await db.flush()
    return c.id


async def test_executor_for_neca_ocn_2_creates_two_pdf_attachments(db: AsyncSession):
    cid = await _seed_amano(db)
    appr = Approval(
        task_id=uuid.uuid4(),
        action_type="queue_filing_submission",
        tier=TIER_SIGN_PAY,
        payload={
            "form": "NECA-OCN-2",
            "applicant_legal_name": "Amano Telecom LLC",
            "ein": "39-2196239",
            "frn": "0037045218",
            "letter_of_agency": {
                "grantor": "Amano Telecom LLC",
                "grantee": "Switchboard",
                "signatory_name": "AMBER SIDNEY HUNT",
                "effective_date": "2026-05-20",
            },
        },
        decision=DECISION_APPROVED,
    )
    db.add(appr)
    await db.flush()

    result = await execute_approval(appr, cid, db)
    assert "NECA-OCN-2 (v1)" in result
    assert "2 PDF attachment(s)" in result

    # Two Document rows now exist with real bytes.
    from sqlalchemy import select  # noqa: PLC0415

    docs = (
        await db.scalars(select(Document).where(Document.client_id == cid))
    ).all()
    assert len(docs) == 2
    for d in docs:
        assert d.s3_key is not None
        assert d.size_bytes and d.size_bytes > 1000
        assert d.mime == "application/pdf"
        assert d.filename and d.filename.endswith(".pdf")

    # email_packet.attachments references both Document IDs.
    packet = appr.email_packet
    assert packet is not None
    atts = packet.get("attachments") or []
    assert len(atts) == 2
    assert {a["filename"] for a in atts} == {d.filename for d in docs}


async def test_executor_for_fcc_499_creates_single_generic_pdf(db: AsyncSession):
    """Forms without a dedicated builder fall back to the generic
    key/value dump so we never leave a Document Hub stub with no
    file. This is the regression guard for the prior 'no file'
    state on the FCC 499-A row."""
    cid = await _seed_amano(db)
    appr = Approval(
        task_id=uuid.uuid4(),
        action_type="queue_filing_submission",
        tier=TIER_SIGN_PAY,
        payload={"form": "FCC 499-A", "frn": "0037045218"},
        decision=DECISION_APPROVED,
    )
    db.add(appr)
    await db.flush()

    await execute_approval(appr, cid, db)

    from sqlalchemy import select  # noqa: PLC0415

    docs = (
        await db.scalars(select(Document).where(Document.client_id == cid))
    ).all()
    assert len(docs) == 1
    assert docs[0].s3_key is not None
    assert docs[0].size_bytes and docs[0].size_bytes > 500
    atts = (appr.email_packet or {}).get("attachments") or []
    assert len(atts) == 1


# --- HTTP: send-email actually attaches the PDFs ---------------------


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

    async def _override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = _override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c, maker
    app.dependency_overrides.clear()
    await engine.dispose()


async def test_send_email_attaches_pdfs_to_smtp_message(http):
    """End-to-end: approve a NECA-OCN-2 → executor generates PDFs →
    /send-email loads them from storage and passes them to the SMTP
    send call. We patch the actual socket so this stays offline."""
    c, maker = http
    reg = await c.post(
        "/auth/register",
        json={"email": "op@a.com", "password": "supersecret", "name": "Op"},
    )
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    cid = uuid.UUID(
        (await c.post("/clients", headers=h, json={"name": "Amano"})).json()["id"]
    )

    # Seed intake, project, pending approval, then approve it via the
    # executor so the email_packet.attachments are populated.
    async with maker() as s:
        s.add(
            ClientIntake(
                client_id=cid,
                legal_name="Amano Telecom LLC",
                ein="39-2196239",
                officer_email="amber@amanotelecom.com",
            )
        )
        p = Project(client_id=cid)
        s.add(p)
        await s.flush()
        t = Task(
            project_id=p.id,
            agent="carrier",
            status="awaiting_approval",
            input={"instruction": "Draft NECA-OCN-2"},
        )
        s.add(t)
        await s.flush()
        appr = Approval(
            task_id=t.id,
            action_type="queue_filing_submission",
            tier=TIER_SIGN_PAY,
            payload={
                "form": "NECA-OCN-2",
                "applicant_legal_name": "Amano Telecom LLC",
                "ein": "39-2196239",
                "frn": "0037045218",
            },
            decision=DECISION_PENDING,
        )
        s.add(appr)
        await s.flush()
        aid = appr.id
        # Approve via the same path the operator uses — runs the
        # executor and populates the packet.
        await s.commit()

    await c.post(f"/approvals/{aid}/approve", headers=h, json={})

    # Add the client_email credential so send-email's client path can
    # authenticate. We never open a real SMTP — _send_via_client_sync
    # is patched below.
    async with maker() as s:
        await store_credential(
            s,
            client_id=cid,
            service="client_email",
            username="amber@gmail.com",
            secret="app-pw",
        )
        await s.commit()

    with patch(
        "app.notifications._send_via_client_sync",
        return_value="<msg@test>",
    ) as mocked:
        r = await c.post(f"/approvals/{aid}/send-email", headers=h, json={})

    assert r.status_code == 200
    assert r.json()["sent"] is True
    assert mocked.called
    kwargs = mocked.call_args.kwargs
    attachments = kwargs.get("attachments") or []
    # NECA-OCN-2 yields two PDFs (form + LOA).
    assert len(attachments) == 2
    for filename, data, mime in attachments:
        assert isinstance(data, (bytes, bytearray))
        assert data[:5] == b"%PDF-"
        assert mime == "application/pdf"
        assert filename.endswith(".pdf")
