from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import platform_config
from app.api.deps import get_current_user
from app.api.routes import (
    agents,
    approvals,
    audit,
    auth,
    briefing,
    client_links,
    client_portal,
    clients,
    credentials,
    intake,
    portal_actions,
    tasks,
)
from app.audit import record_audit
from app.config import settings
from app.db import engine, get_db
from app.models import User
from app.notifications import send_email

app = FastAPI(title="Switchboard API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(clients.router)
app.include_router(intake.router)
app.include_router(tasks.router)
app.include_router(agents.router)
app.include_router(approvals.router)
app.include_router(briefing.router)
app.include_router(audit.router)
app.include_router(credentials.router)
app.include_router(portal_actions.router)
app.include_router(client_links.router)
app.include_router(client_portal.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}


@app.get("/health/db")
async def health_db() -> dict[str, str]:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok", "database": "reachable"}


class PlatformConfigUpdate(BaseModel):
    """Only the keys present are touched. Set a key to empty string to
    clear the DB row (the env fallback then applies)."""

    anthropic_api_key: str | None = Field(default=None, max_length=512)
    agent_model: str | None = Field(default=None, max_length=128)
    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_user: str | None = Field(default=None, max_length=255)
    smtp_password: str | None = Field(default=None, max_length=512)
    smtp_from: str | None = Field(default=None, max_length=255)
    smtp_use_tls: bool | None = None
    app_base_url: str | None = Field(default=None, max_length=255)


class PlatformConfigTestEmail(BaseModel):
    to: str = Field(min_length=3, max_length=255)


@app.get("/system/status")
async def system_status(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Platform readiness — what's wired up. Auth-gated; reveals which
    integrations are configured but NEVER the secret values themselves.
    Reads from the platform_settings DB rows first (GUI-edited) and
    falls back to the static env config."""
    snap = await platform_config.status_snapshot(db)
    return {
        "environment": settings.environment,
        "documents_dir": settings.documents_dir,
        "portal_integration_backend": settings.portal_integration_backend,
        **snap,
    }


@app.put("/system/config", status_code=status.HTTP_204_NO_CONTENT)
async def update_platform_config(
    body: PlatformConfigUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Save runtime config via the GUI. Each field that is not None is
    persisted (encrypted for secrets); an empty string clears the value
    so the env fallback applies. Audited but the secret bytes are never
    written to the audit row."""
    payload = body.model_dump(exclude_unset=True)
    if not payload:
        return
    audit_after: dict[str, object] = {}
    for key, value in payload.items():
        if key == "smtp_port" and value is not None:
            value = str(value)
        elif key == "smtp_use_tls" and value is not None:
            value = "true" if value else "false"
        await platform_config.set_value(db, key, value)
        # Audit records whether the key was set or cleared — never the bytes.
        audit_after[key] = (
            "[cleared]" if value in (None, "") else
            "[set]" if key in platform_config.SECRET_KEYS else value
        )
    await record_audit(
        db,
        actor=user.email,
        action="platform.config_updated",
        subject="platform_settings",
        after=audit_after,
    )
    await db.commit()


@app.post("/system/config/test-email", status_code=status.HTTP_200_OK)
async def test_email(
    body: PlatformConfigTestEmail,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Send a test email using the current SMTP config (DB → env
    fallback). Lets the operator verify the GUI-entered settings work
    before relying on them for approval notifications."""
    host = await platform_config.smtp_host(db)
    sender = await platform_config.smtp_from(db)
    if not (host and sender):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SMTP host and from address must be set first.",
        )
    await send_email(
        db,
        body.to,
        "Switchboard test email",
        f"This is a test email from Switchboard, sent by {user.email}.",
    )
    return {"status": "sent (delivery is best-effort; check your inbox)"}
