import json
import os
import socket
import ssl
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import platform_config
from app.api.deps import get_current_user
from app.api.routes import (
    activity,
    agents,
    applications,
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

# --- Sentry (optional error tracking) --------------------------------------
# Initialized before the FastAPI app so its middleware wraps the whole
# request stack. Empty DSN = no-op. Use settings.sentry_traces_sample_rate
# to enable performance traces in production (default off — agent runs
# can be expensive, so don't sample 100%).
if settings.sentry_dsn:
    import sentry_sdk  # noqa: PLC0415

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        release=settings.app_version,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,  # never ship user emails / IPs to Sentry
    )


app = FastAPI(title="Switchboard API", version=settings.app_version)

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
app.include_router(applications.router)
app.include_router(activity.router)


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
        "app_version": settings.app_version,
        # Railway sets these on every deploy. Empty in local dev.
        "git_commit": (settings.railway_git_commit_sha or "")[:12] or None,
        "git_branch": settings.railway_git_branch or None,
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


# --- /system/update — check + request update via host cron -----------------

_GITHUB_API = "https://api.github.com/repos/{repo}/commits/main"


async def _fetch_latest_main_commit(repo: str) -> tuple[dict | None, str | None]:
    """Single point of network I/O so tests can mock it without
    monkey-patching httpx itself (which the test client also uses).
    Returns (payload, error_reason)."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                _GITHUB_API.format(repo=repo),
                headers={"Accept": "application/vnd.github+json"},
            )
        if r.status_code == 200:
            return r.json(), None
        return None, f"GitHub API returned {r.status_code}"
    except Exception as exc:
        return None, f"GitHub API call failed: {exc.__class__.__name__}"


@app.get("/system/update")
async def update_status(
    _: User = Depends(get_current_user),
) -> dict[str, object]:
    """Compare the live commit to the latest commit on origin/main.

    Calls the GitHub public API (no auth — 60 req/hr per IP) and returns
    whether the deploy is behind, the latest commit message, and whether
    an update is already queued via the sentinel file the host's cron
    polls. Network failures are reported gracefully so the GUI still
    renders something useful even when GitHub is unreachable."""
    current = (settings.railway_git_commit_sha or "").strip()
    current_short = current[:12] if current else None
    sentinel = Path(settings.update_request_file)
    pending = sentinel.exists()
    pending_meta: dict | None = None
    if pending:
        try:
            pending_meta = json.loads(sentinel.read_text())
        except Exception:
            pending_meta = None

    latest, reason = await _fetch_latest_main_commit(settings.github_repo)

    body: dict[str, object] = {
        "repo": settings.github_repo,
        "current_commit": current_short,
        "update_pending": pending,
        "update_pending_meta": pending_meta,
    }
    if latest is None:
        body["latest_available"] = None
        body["up_to_date"] = None
        body["check_error"] = reason
        return body
    latest_sha = (latest.get("sha") or "")[:12]
    body["latest_available"] = {
        "sha": latest_sha,
        "message": (latest.get("commit", {}).get("message") or "").split("\n")[0],
        "author": (
            latest.get("commit", {}).get("author", {}).get("name")
            or latest.get("author", {}).get("login")
            or ""
        ),
        "date": latest.get("commit", {}).get("committer", {}).get("date"),
        "url": latest.get("html_url"),
    }
    body["up_to_date"] = (
        current_short is not None and current_short == latest_sha
    )
    return body


@app.post("/system/update/request", status_code=status.HTTP_202_ACCEPTED)
async def request_update(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Queue an update by writing a sentinel file the host's update.sh
    cron polls. The actual `git pull && docker compose up --build` runs
    on the host (the api container can't reach the docker socket from
    inside itself, by design). Idempotent — repeated requests
    overwrite the timestamp."""
    sentinel = Path(settings.update_request_file)
    try:
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text(
            json.dumps(
                {
                    "requested_by": user.email,
                    "requested_at": datetime.now(UTC).isoformat(),
                }
            )
        )
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Could not write update sentinel. On a VPS deploy this "
                "directory should be bind-mounted from the host. Path: "
                f"{settings.update_request_file}. Error: {exc}"
            ),
        ) from exc
    await record_audit(
        db,
        actor=user.email,
        action="system.update_requested",
        subject="platform",
        after={"sentinel_path": str(sentinel)},
    )
    await db.commit()
    return {
        "status": "queued",
        "sentinel": str(sentinel),
        "note": (
            "The host's update.sh cron will pick this up on its next "
            "tick (default 5 min). Reload Settings to see the new "
            "commit appear."
        ),
    }


@app.delete(
    "/system/update/request", status_code=status.HTTP_204_NO_CONTENT
)
async def cancel_update_request(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Cancel a queued update before the host cron picks it up."""
    sentinel = Path(settings.update_request_file)
    if sentinel.exists():
        try:
            sentinel.unlink()
        except OSError:
            pass
        await record_audit(
            db,
            actor=user.email,
            action="system.update_cancelled",
            subject="platform",
        )
        await db.commit()


# --- /system/certificate — TLS cert for the public app URL -----------------


@app.get("/system/certificate")
async def certificate_info(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Live HTTPS cert info for the operator's public URL. Connects from
    the API container to its own public domain, reads the peer cert,
    surfaces issuer + expiry + days remaining + alt names. Useful for
    confirming Caddy auto-renewal is working (or that DNS/Cloudflare
    is misconfigured)."""
    base = await platform_config.app_base_url(db)
    if not base or not base.startswith("https://"):
        return {
            "available": False,
            "reason": "No public HTTPS URL configured. Set APP_BASE_URL.",
        }
    host = urlparse(base).hostname
    if not host:
        return {"available": False, "reason": "Could not parse APP_BASE_URL."}
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert() or {}
    except Exception as exc:
        return {
            "available": False,
            "reason": f"Could not reach {host}:443 — {exc.__class__.__name__}: {exc}",
        }

    # cert['notAfter'] format: 'Aug 20 12:00:00 2026 GMT'
    not_after_str = cert.get("notAfter")
    if not not_after_str:
        return {"available": False, "reason": "Cert has no notAfter field."}
    not_after = datetime.strptime(not_after_str, "%b %d %H:%M:%S %Y %Z").replace(
        tzinfo=UTC
    )
    days_left = (not_after - datetime.now(UTC)).days
    subject = dict(x[0] for x in cert.get("subject", ()))
    issuer = dict(x[0] for x in cert.get("issuer", ()))
    alt_names = [n[1] for n in cert.get("subjectAltName", ()) if n[0] == "DNS"]
    return {
        "available": True,
        "host": host,
        "subject_cn": subject.get("commonName"),
        "issuer_o": issuer.get("organizationName"),
        "issuer_cn": issuer.get("commonName"),
        "not_after": not_after.isoformat(),
        "days_until_expiry": days_left,
        "alt_names": alt_names,
        "auto_renewed_by": (
            "Caddy" if "let's encrypt" in (issuer.get("organizationName") or "").lower() else "external"
        ),
    }


# --- /health/detailed — self-test ------------------------------------------


@app.get("/health/detailed")
async def health_detailed(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Per-subsystem health check. Used by the Settings page's
    'Run self-test' button so the operator can diagnose problems
    without SSH'ing into the VPS. Auth-gated; reveals no secrets."""
    checks: dict[str, dict[str, object]] = {}

    # 1. Database: reachable + responsive.
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = {"ok": True}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": str(exc)[:200]}

    # 2. Documents storage: directory exists + writable.
    try:
        docs_dir = Path(settings.documents_dir)
        docs_dir.mkdir(parents=True, exist_ok=True)
        test_path = docs_dir / ".healthcheck"
        test_path.write_text("ok")
        content = test_path.read_text()
        test_path.unlink(missing_ok=True)
        checks["document_storage"] = {
            "ok": content == "ok",
            "path": str(docs_dir),
        }
    except Exception as exc:
        checks["document_storage"] = {"ok": False, "error": str(exc)[:200]}

    # 3. Anthropic key configured (we don't actually call the API —
    #    that would cost money on every self-test).
    key = await platform_config.anthropic_api_key(db)
    checks["anthropic_key"] = {
        "ok": bool(key),
        "note": "key present" if key else "agent runs return 503 until set",
    }

    # 4. SMTP configured (also no actual send).
    smtp_host = await platform_config.smtp_host(db)
    smtp_from = await platform_config.smtp_from(db)
    checks["smtp"] = {
        "ok": bool(smtp_host and smtp_from),
        "note": "configured" if (smtp_host and smtp_from) else "not configured (in-app badge only)",
    }

    # 5. Update sentinel directory writable (only matters on self-hosted).
    sentinel = Path(settings.update_request_file)
    try:
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        # touch a probe file
        probe = sentinel.parent / ".probe"
        probe.write_text("ok")
        probe.unlink(missing_ok=True)
        checks["update_channel"] = {"ok": True, "path": str(sentinel.parent)}
    except Exception as exc:
        checks["update_channel"] = {
            "ok": False,
            "error": str(exc)[:200],
            "note": "GUI updates won't work; SSH + bash deploy/update.sh still does.",
        }

    overall = all(c.get("ok") for c in checks.values())
    return {"overall_ok": overall, "checks": checks}
