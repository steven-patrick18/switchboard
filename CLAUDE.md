# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Switchboard is an AI-operator platform for US VoIP company launches: a human operator supervises Claude agents that draft/queue work; every regulated or external action waits in an approval queue; everything is audited. Monorepo: `apps/web` (Next.js 14 operator UI), `apps/api` (FastAPI backend + agent runtime). See `README.md` for product framing and `docs/Switchboard.pptx` for the full brief.

## Commands

All backend commands run from `apps/api` using the project venv (`.venv`). Local dev uses **SQLite** (no Docker needed); production is Postgres.

```
# one-time / after dependency changes
.venv/Scripts/python.exe -m pip install -e ".[dev]"

# apply migrations to a DB (set DATABASE_URL for SQLite; omit for Postgres default)
DATABASE_URL="sqlite+aiosqlite:///./dev.db" .venv/Scripts/python.exe -m alembic upgrade head

# run the API
DATABASE_URL="sqlite+aiosqlite:///./dev.db" .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# full test suite / a single test
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m pytest -q tests/test_audit.py::test_audit_trail

# end-to-end HTTP smoke test (prints SMOKE PASS)
rm -f smoke.db && DATABASE_URL="sqlite+aiosqlite:///./smoke.db" .venv/Scripts/python.exe -m alembic upgrade head \
  && DATABASE_URL="sqlite+aiosqlite:///./smoke.db" .venv/Scripts/python.exe scripts/smoke.py
```

Web (from `apps/web`): `npm install`; `npm run dev` (port 3000); type-check with `npx tsc --noEmit`. Ruff is configured (line-length 100) for the API.

`pytest` works without env vars: `tests/conftest.py` sets `DATABASE_URL` to in-memory SQLite so importing `app.db` (which builds an engine at import) does not require the Postgres driver. Tests then use their own in-memory engine via `app.dependency_overrides[get_db]`.

## Critical operational gotchas (non-obvious, discovered)

- **`uvicorn --reload` does NOT reliably hot-reload in this environment.** After any backend change, **stop and restart the API process** or it serves stale code (routes will 404). The Next dev server *does* hot-reload.
- **Migrations are not auto-applied on boot.** After adding a migration, run `alembic upgrade head` against every target DB, including the local `dev.db`. Latest revision: `0009` (client share links).
- **CORS**: browser calls need the request origin in `settings.cors_origin_list` (`app/config.py`, default `localhost:3000` / `127.0.0.1:3000`). A missing origin shows as "Failed to fetch" in the browser but passes curl/ASGI tests.
- **Agents require `ANTHROPIC_API_KEY`** (repo-root `.env`; config reads `.env` and `../../.env`). Without it, agent-run endpoints return 503 by design (`get_anthropic_client` in `app/llm.py`). Auth, intake, approvals, audit, document upload, settings, badge polling all work keyless.
- Passwords use **bcrypt directly** with a SHA-256 prehash (`app/security.py`) — not passlib (passlib breaks on modern bcrypt). Pydantic `EmailStr` rejects reserved TLDs (`.test`, `.local`, `example.com`); use a real domain in tests/fixtures.
- **Document storage is content-addressed on local disk** under `settings.documents_dir` (default `.documents/`). The SHA-256 hex is stored as `Document.s3_key`; two identical uploads share one on-disk file. Swap `app/storage.py` for S3 in prod without touching the routers.
- **`apiFetch` (web) auto-sets `Content-Type: application/json` unless the body is `FormData`** — required so the browser supplies the multipart boundary on document upload.
- **Reject requires a non-blank reason at the schema level** (`RejectBody` / `BatchBody` validators). The audit trail must explain why something did NOT happen, not just why it did.
- `*.db`, `.env`, `.venv/`, `node_modules/`, `.claude/`, `.documents/` are gitignored.

## Architecture — the big picture

The invariant: **agents propose, the operator approves, the platform executes, everything is audited.** These pieces span multiple files:

1. **Agent runtime + approval gateway** — `app/agents/base.py::run_agent` is a manual Claude tool-use loop. Tools (`app/agents/tools.py`, `Tool` dataclass) are tier-classified using constants in `app/models/approval.py`: **T0/T1 run inline; T2/T3 are intercepted → an `Approval` (decision=pending) is created and the action does NOT execute.** This is the core safety boundary — never bypass it. A tool is either pure (`runner(args)`) or workspace-aware (`db_runner(args, ToolContext)` — async, receives db/client_id/project_id). Each agent has a scoped tool whitelist; prompt caching on the frozen system prompt; adaptive thinking; per-run token/cost in `AgentRun`.
2. **Agents** — `app/agents/registry.py` (`pm`, `compliance`, `document`). PM (`app/agents/pm.py`) orchestrates: `check_intake_status` (T0) + `assign_task` (T1) creates queued sub-`Task`s for compliance/document; PM itself takes no external action.
3. **Execution on approval** — `app/execution.py::execute_approval`: approving/editing an `Approval` runs an executor keyed by `action_type` that performs the **in-platform** follow-through (materializes a versioned `Document`) and records the result. There is intentionally **no real external I/O** (FCC submission / Documenso e-sign are later integration layers; the recorded result says so). Wired in `app/api/routes/approvals.py` for single and batch.
4. **Capture-once intake** — `app/intake.py` is the single source of truth. `resolve_required_documents(intake)` **auto-decides the mandated documents per client** from the intake (international → Section 214; target states → notarized state CPCN). Each doc carries `mandatory` (UI `*`) and `needs_scan`. Submit is blocked until complete. Agents read the same intake to avoid re-asking the client.
5. **Document specs / request pack** — `app/doc_samples.py`: per-document client-facing specs and `build_request_pack_pdf()` (reportlab) — one tailored, client-ready PDF.
6. **Document upload + storage** — `app/storage.py` is content-addressed local disk (swap for S3 in prod). `POST /clients/{id}/documents` is multipart (form `type` + `file`); `GET /clients/{id}/documents/{doc_id}/download` streams the bytes back with the original filename + mime. The intake completeness check only counts a document type as provided once bytes are on file (`Document.s3_key IS NOT NULL`) — metadata stubs don't satisfy a mandate.
7. **Audit trail** — `app/audit.py::record_audit` is append-only (no update/delete path = the legal record). Emitted across the approval lifecycle (queued by agent → approved/edited/rejected by operator → executed by system, incl. batch). CSV export at `GET /audit.csv` (operator-wide, owner-scoped) and `GET /clients/{id}/audit.csv` (per-client). Full archive zip at `GET /clients/{id}/documents.zip` bundles `intake.json` + `audit.csv` + the latest version of every uploaded document under `documents/{type}/`.
8. **Briefing + sidebar badge** — `app/api/routes/briefing.py` is the deterministic (no LLM) operator-wide digest; `GET /approvals/count` is the light counter the sidebar polls every 30s to show pending approvals at a glance from any page.
9. **Settings** — `PUT /auth/me` for display-name rename, `POST /auth/change-password` verifies current secret + audits the event (`account.password_changed`) without ever logging the secret.
10. **Notifications** — `app/notifications.py` is a best-effort SMTP wrapper. `app/agents/base.py` calls `notify_approval_queued(...)` immediately after queuing a tier-2/3 approval. SMTP unconfigured (empty `SMTP_HOST`/`SMTP_FROM`) → silent no-op. SMTP errors are logged and swallowed so the agent loop is never broken by mail-server flakiness.
11. **Public client portal** — `app/api/routes/client_links.py` (operator: create/list/revoke) + `app/api/routes/client_portal.py` (public, token-scoped). The operator generates a magic link from the client page ("Client share links" section); the client opens `/c/{token}` and fills intake / uploads docs / drops in portal credentials without seeing anything outside their own client. Every write audits as actor `client:{link_id}`. Credentials are write-only through the portal — the client lists service names but never sees stored secret values; that's still operator-only. Links can carry an `expires_in_hours` or be explicitly revoked; `last_used_at` is touched on every successful resolution so the operator sees activity.
10. **Web** — Next.js App Router, token in `localStorage`, `lib/api.ts` (`apiFetch` w/ FormData passthrough, `downloadFile`, `fetchBlob`). All authenticated pages live inside `AppShell` (fixed left sidebar w/ pending-approval badge + operator profile, `max-w-7xl` content). Pages: `/login`, `/dashboard` (briefing + audit-export), `/workspaces`, `/clients/[id]` (intake wizard via `IntakeForm.tsx`, document upload with per-row "+ upload" / preview modal, credentials vault, agent runner, audit trail), `/approvals`, `/history`, `/settings`.

## Conventions

- **Operator scoping**: every client-data query filters by `Client.owner_id == current_user.id` (Approval/Task/Audit join Task→Project→Client). Cross-operator access returns **404**, not 403.
- **State-transition guards are load-bearing**: approvals are pending-only (409 if re-decided); a task is queued-only to run (409 otherwise). Keep these.
- **Single sources of truth — change these, not call sites**: `app/intake.py` (required fields + per-client doc resolution), `app/agents/registry.py` (agents), `app/models/approval.py` (tiers/decisions), `app/doc_samples.py` (doc specs).
- **Migrations are hand-authored** to match the SQLAlchemy models (sequential `0001…`, chained `down_revision`). SQLite in tests does not enforce FKs, so tests may use bare UUIDs for FK columns.
- **Verification for every change**: `pytest -q` + `scripts/smoke.py` + `npx tsc --noEmit` green, then **restart the API** and verify in the running preview. Tests never spend API credits — inject a fake Anthropic client via `app.dependency_overrides[get_anthropic_client]` (or pass a fake to `run_agent`) and override `get_db` with an in-memory SQLite engine.
- E-signature provider is **self-hosted Documenso** (`.env.example`); do not reintroduce DocuSign/SaaS without checking.
