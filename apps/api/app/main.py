from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.deps import get_current_user
from app.api.routes import (
    agents,
    approvals,
    audit,
    auth,
    briefing,
    clients,
    credentials,
    intake,
    portal_actions,
    tasks,
)
from app.config import settings
from app.db import engine
from app.models import User

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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}


@app.get("/health/db")
async def health_db() -> dict[str, str]:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok", "database": "reachable"}


@app.get("/system/status")
async def system_status(
    _: User = Depends(get_current_user),
) -> dict[str, object]:
    """Platform readiness — what's wired up. Auth-gated; reveals which
    integrations are configured but NEVER the secrets themselves. Used
    by the Settings page so the operator doesn't find out the hard way
    that the Anthropic key is missing or SMTP isn't set."""
    return {
        "environment": settings.environment,
        "agent_model": settings.agent_model,
        "anthropic": bool(settings.anthropic_api_key),
        "smtp": bool(settings.smtp_host and settings.smtp_from),
        "smtp_host": settings.smtp_host or None,
        "smtp_from": settings.smtp_from or None,
        "app_base_url": settings.app_base_url or None,
        "documents_dir": settings.documents_dir,
        "portal_integration_backend": settings.portal_integration_backend,
    }
