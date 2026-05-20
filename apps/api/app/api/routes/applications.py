"""Per-filing stage tracker endpoints.

GET    /clients/{id}/applications              — list with derived labels.
POST   /clients/{id}/applications/sync         — idempotent: ensure every
                                                  intake-derived application
                                                  exists for this client.
POST   /clients/{id}/applications              — manually add one (e.g. a
                                                  carrier interconnect that
                                                  the operator decided to pursue).
PATCH  /clients/{id}/applications/{app_id}     — update stage / agent /
                                                  notes / external_ref.
DELETE /clients/{id}/applications/{app_id}     — remove (only useful when
                                                  the operator changed their
                                                  mind about pursuing it).
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import readiness as readiness_mod
from app.api.deps import get_current_user
from app.applications import (
    default_agent_for,
    label_for,
    resolve_required_applications,
    spec_for,
)
from app.audit import record_audit
from app.db import get_db
from app.models import Application, Client, ClientIntake, User
from app.models.application import ALL_STAGES, STAGE_NOT_STARTED
from app.schemas.applications import (
    ApplicationCreate,
    ApplicationOut,
    ApplicationResolveResult,
    ApplicationUpdate,
)
from app.schemas.readiness import ReadinessItemOut, ReadinessSnapshotOut

router = APIRouter(prefix="/clients/{client_id}/applications", tags=["applications"])


async def _owned_client(
    client_id: uuid.UUID, user: User, db: AsyncSession
) -> Client:
    client = await db.get(Client, client_id)
    if client is None or client.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Client not found"
        )
    return client


def _to_out(row: Application) -> ApplicationOut:
    spec = spec_for(row.type)
    return ApplicationOut(
        id=row.id,
        client_id=row.client_id,
        type=row.type,
        label=spec.label if spec else row.type,
        description=spec.description if spec else "",
        default_agent=spec.default_agent if spec else "pm",
        stage=row.stage,
        current_agent=row.current_agent,
        notes=row.notes,
        external_ref=row.external_ref,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/readiness", response_model=ReadinessSnapshotOut)
async def launch_readiness(
    client_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReadinessSnapshotOut:
    """Deterministic 'what can we start right now?' answer for the
    operator's UI. Splits every application into ready / blocked /
    in_flight / complete based on intake completeness + per-app
    prereqs (CORES → OCN → 499 → RMD → STIR/SHAKEN, etc.). The Start
    button in the GUI uses owner_agent + instruction to kick off the
    right agent with a pre-filled prompt."""
    await _owned_client(client_id, user, db)
    snap = await readiness_mod.compute(client_id, db)
    def _to_out(items):
        return [
            ReadinessItemOut(
                application_type=i.application_type,
                application_id=i.application_id,
                label=i.label,
                owner_agent=i.owner_agent,
                instruction=i.instruction,
                stage=i.stage,
                current_agent=i.current_agent,
                blocked_on=i.blocked_on,
                external_ref=i.external_ref,
                notes=i.notes,
            )
            for i in items
        ]
    return ReadinessSnapshotOut(
        intake_complete=snap.intake_complete,
        intake_missing_fields=snap.intake_missing_fields,
        intake_missing_documents=snap.intake_missing_documents,
        ready=_to_out(snap.ready),
        blocked=_to_out(snap.blocked),
        in_flight=_to_out(snap.in_flight),
        complete=_to_out(snap.complete),
    )


@router.get("", response_model=list[ApplicationOut])
async def list_applications(
    client_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApplicationOut]:
    await _owned_client(client_id, user, db)
    rows = list(
        (
            await db.scalars(
                select(Application)
                .where(Application.client_id == client_id)
                .order_by(Application.created_at)
            )
        ).all()
    )
    return [_to_out(r) for r in rows]


@router.post("/sync", response_model=ApplicationResolveResult)
async def sync_from_intake(
    client_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApplicationResolveResult:
    """Idempotent. Looks at the client's intake, computes the required
    application set, and creates rows for any that don't exist yet.
    Existing rows are untouched — re-run any time you change the intake
    (e.g. added a state to target_states) to pick up the new mandates."""
    await _owned_client(client_id, user, db)
    intake = await db.scalar(
        select(ClientIntake).where(ClientIntake.client_id == client_id)
    )
    needed = resolve_required_applications(intake)
    existing_rows = list(
        (
            await db.scalars(
                select(Application).where(Application.client_id == client_id)
            )
        ).all()
    )
    existing_types = {r.type for r in existing_rows}
    created: list[str] = []
    for t in needed:
        if t in existing_types:
            continue
        db.add(
            Application(
                client_id=client_id,
                type=t,
                stage=STAGE_NOT_STARTED,
                current_agent=None,
            )
        )
        created.append(t)
    if created:
        await record_audit(
            db,
            actor=user.email,
            action="application.synced",
            subject=f"client:{client_id}",
            client_id=client_id,
            after={"created": created},
        )
    await db.commit()
    return ApplicationResolveResult(
        created=created, existing=sorted(existing_types)
    )


@router.post(
    "", response_model=ApplicationOut, status_code=status.HTTP_201_CREATED
)
async def create_application(
    client_id: uuid.UUID,
    body: ApplicationCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApplicationOut:
    """Manually add a single application — most common case: a carrier
    interconnect (carrier:bandwidth) that the operator chose."""
    await _owned_client(client_id, user, db)
    if spec_for(body.type) is None and not (
        body.type.startswith("state_cpcn:") or body.type.startswith("carrier:")
    ):
        # Don't 400 — let the operator add arbitrary types if they want,
        # but flag in the audit so it's traceable.
        pass
    row = Application(
        client_id=client_id,
        type=body.type,
        stage=STAGE_NOT_STARTED,
        notes=body.notes,
    )
    db.add(row)
    await db.flush()
    await record_audit(
        db,
        actor=user.email,
        action="application.created",
        subject=f"application:{row.id}",
        client_id=client_id,
        after={"type": body.type},
    )
    await db.commit()
    await db.refresh(row)
    return _to_out(row)


@router.patch("/{app_id}", response_model=ApplicationOut)
async def update_application(
    client_id: uuid.UUID,
    app_id: uuid.UUID,
    body: ApplicationUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApplicationOut:
    """Update stage / current_agent / notes / external_ref. Every change
    audits the before/after pair so the trail explains the full
    lifecycle of every filing."""
    await _owned_client(client_id, user, db)
    row = await db.get(Application, app_id)
    if row is None or row.client_id != client_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Application not found"
        )
    payload = body.model_dump(exclude_unset=True)
    if "stage" in payload and payload["stage"] not in ALL_STAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown stage; must be one of {sorted(ALL_STAGES)}.",
        )
    before = {k: getattr(row, k) for k in payload}
    for k, v in payload.items():
        setattr(row, k, v)
    row.updated_at = datetime.now(UTC)
    await record_audit(
        db,
        actor=user.email,
        action="application.updated",
        subject=f"application:{row.id}",
        client_id=client_id,
        before=before,
        after=payload,
    )
    await db.commit()
    await db.refresh(row)
    return _to_out(row)


@router.delete("/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_application(
    client_id: uuid.UUID,
    app_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _owned_client(client_id, user, db)
    row = await db.get(Application, app_id)
    if row is None or row.client_id != client_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Application not found"
        )
    row_type = row.type
    await db.delete(row)
    await record_audit(
        db,
        actor=user.email,
        action="application.deleted",
        subject=f"application:{app_id}",
        client_id=client_id,
        after={"type": row_type},
    )
    await db.commit()
