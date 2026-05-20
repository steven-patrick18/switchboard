# Changelog

All notable changes to Switchboard. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions
are major milestones, not formal releases — every push to `main`
auto-deploys to Railway, so the commit hash is the real version
identifier (see Settings → Platform readiness in the running app).

## v1.2.0 — Update + HTTPS + self-test in the GUI · 2026-05-20

### Added — Production-operations from the Settings page
- **System updates** card — shows live commit vs. latest on
  `origin/main` (queried from GitHub's public API), one-click
  **Apply update** writes a sentinel file the host's `update.sh`
  cron picks up on its next tick. **Cancel** unwrites it before
  the cron fires. Audited as `system.update_requested` /
  `system.update_cancelled`.
- **HTTPS certificate** card — connects from the API to its own
  public URL, reads the live cert, surfaces issuer, expiry date,
  days-until-expiry (with green/amber/red pill), covered domains,
  and whether Caddy auto-renews. Confirms TLS health at a glance
  without SSH.
- **System self-test** card — five subsystem checks (database,
  document storage, Anthropic key configured, SMTP configured,
  update channel writable) returned by `/health/detailed`. Each
  check shows ok/fail + a one-line explanation, so the operator
  can diagnose "what's not working" from the browser.

### Added — Production hardening
- **Sentry** integration via optional `SENTRY_DSN` env var. Empty
  = no-op. Configured with `send_default_pii=False` (Sentry never
  sees operator emails or IPs) and release tag = `app_version`.
- New `app_version` bumped to `1.2.0`.
- deploy/install.sh creates `/var/run/switchboard` on the VPS so
  the GUI's "Apply update" can drop sentinels there. Compose binds
  the same directory into the api container.
- deploy/update.sh checks the sentinel and runs immediately when
  present (otherwise it's the regular git-ahead check). Safe in
  cron — no-op when nothing's pending.

### Wired
- `GET /system/update` — current vs. latest commit + pending status
- `POST /system/update/request` — queue a deploy (202 + sentinel)
- `DELETE /system/update/request` — cancel a queued deploy
- `GET /system/certificate` — read peer cert from the public URL
- `GET /health/detailed` — per-subsystem self-test

98 backend tests pass; tsc clean.

## v1.1.0 — Self-hosted VPS deploy + update flow · 2026-05-20

### Added
- **`deploy/docker-compose.yml`** — full stack (postgres + api + web
  + Caddy reverse proxy) with named volumes for data persistence.
- **`deploy/Caddyfile`** — automatic Let's Encrypt HTTPS, no certbot
  needed; Caddy handles renewal in the background.
- **`deploy/install.sh`** — idempotent first-time install. Installs
  Docker if missing, generates `POSTGRES_PASSWORD` + `APP_SECRET_KEY`,
  prompts for domains/email, builds + starts the stack.
- **`deploy/update.sh`** — one-line update after each git push:
  fast-forwards `main`, rebuilds only changed containers, restarts
  in dependency order. No-ops when nothing changed (safe in cron).
- **`deploy/backup.sh`** — daily Postgres dump + documents-volume
  tar. Keeps last 14 days.
- **`docs/VPS_DEPLOY.md`** — non-coder walkthrough: DNS → SSH →
  install → verify → updates → backups → day-2 operations.

### Why
You can now self-host on any Ubuntu/Debian VPS in ~20 minutes
instead of using Railway. Update flow stays simple:
`git push` → `bash deploy/update.sh` on the VPS.

## v1.0.0 — Ready for first paying customer · 2026-05-20

The platform is end-to-end usable for a real US VoIP launch. The
operator can sign up, onboard a client through a magic-link portal,
let six AI agents draft regulated filings, review every gated
action in the approval queue, track each filing's lifecycle stage,
and export a compliance archive when the launch is done. Agents
learn from operator corrections via in-context lessons.

### Added — The mandatory five
- **FCC CORES (FRN)** is now a first-class application alongside
  OCN, FCC 499, RMD, and STIR/SHAKEN. Auto-derived from intake at
  entity stage. (`491dd62`)
- VoIP launch playbook (v3) and Compliance Agent prompt now spell
  out the five-filing dependency order: CORES → OCN → 499 → RMD →
  STIR/SHAKEN. (`491dd62`)

### Added — Per-filing launch tracker
- New `Application` model tracks every regulated filing per client:
  OCN, FCC 499, RMD, STIR/SHAKEN, Section 214 (if international),
  state CPCN per target state, carrier interconnects. (`1913f96`)
- Stages: not_started → in_progress → awaiting_approval → submitted
  → under_review → complete (and `blocked`). Each row carries an
  external reference number, free-text notes, and the current_agent.
- `update_application_stage` agent tool — agents self-report
  progress as they work, so the UI shows "compliance is working on
  FCC 499" in real time. (`1913f96`)
- `POST /clients/{id}/applications/sync` derives the required
  application set from intake idempotently — re-run any time
  intake changes to pick up new mandates.
- Client detail page gets a new **Launch progress** section with
  stage chips, current-agent chips, and inline stage controls.

### Added — Client onboarding playbook
- `docs/CLIENT_ONBOARDING.md` walks the operator through onboarding
  a real client phase-by-phase: kickoff → capture-once intake →
  sync launch checklist → agents draft → operator reviews → file
  on real portals → wait & track → go-live → archive. (`1913f96`)

## v0.9.0 — Production-deployable · 2026-05-20

### Added — Railway deployment
- Multi-stage Dockerfiles for `apps/api` and `apps/web`. (`5dbeffa`)
- `railway.toml` per service. API entrypoint auto-runs
  `alembic upgrade head` on every boot so a Railway deploy "just
  works" — no manual migration step.
- `docs/DEPLOY.md` — 30-minute non-coder walkthrough: GitHub →
  Railway → Postgres → API → Web → custom domain.
- Honest cleanup of `.env.example` — removed Telegram, Gmail OAuth,
  Documenso, and Sentry placeholders that were never wired.

## v0.8.0 — Six-agent roster + supervised learning · 2026-05-20

### Added — Six agents, distributed by scope (`313e39b`)
| Agent | Scope |
|---|---|
| `pm` | Orchestrator (no external action) |
| `intake` | Capture-once mandate + drafts client emails |
| `compliance` | Federal FCC: CORES, 499, RMD, Section 214 |
| `state_licensing` | Per-state CPCNs / PUC paperwork |
| `carrier` | Wholesale interconnection + OCN + STIR/SHAKEN |
| `document` | MSAs, LOAs, contract drafts, signing |

### Added — Agents learn from operator corrections
- New `AgentLesson` model + `app/agent_learning.py`. (`313e39b`)
- Every approval rejection (with reason) or edit-before-approve
  writes a lesson tagged to the agent that queued the action.
- On the next run, the agent's system prompt is prepended with
  "PAST CORRECTIONS FROM YOUR OPERATOR" — supervised in-context
  learning, no fine-tuning. Owner-scoped.
- GUI: `/agents` page shows a "learned: N" pill per agent + a
  Lessons modal with rejection / edit / manual sources. Operator
  can add manual lessons too.

## v0.7.0 — Everything goes GUI · 2026-05-20

### Added — GUI for AI Agents (`ad48db0`)
- New `/agents` page lets the operator clone & customize a built-in
  agent or compose a brand new one — pick tools from a catalog
  (with T0..T3 tier pills), write the system prompt, choose model
  and effort, enable/disable. No code changes needed.
- Tools stay code-defined for safety; tier classification is the
  load-bearing approval boundary that can't be GUI-redefined.
- `Agent` model owner-scoped; custom agent named the same as a
  built-in takes precedence at run time. Disable to fall back.

### Added — GUI for runtime config (`fdc1129`)
- Settings page can now save the **Anthropic API key**, default
  model, **SMTP** host/port/user/password/from/TLS, and the
  public app URL. No more `.env` editing.
- Secret values are encrypted at rest (Fernet); the API never
  echoes a saved secret. A leak-canary test asserts this.
- `Send test email` button verifies SMTP works before relying on it.
- All getters fall back to env when DB row is empty — existing
  `.env`-based deploys keep working.

## v0.6.0 — Public client portal + platform readiness · 2026-05-20

### Added — Public client portal (`8d4e5f1`)
- Operator generates a **magic link** from the client detail page.
- Client opens `/c/{token}` and fills intake, uploads documents,
  drops in portal credentials — all scoped to that one client.
- Every action audits as `client:{link_id}` so the operator's
  trail shows who did what.
- Credentials are write-only through the portal; clients can list
  service names already on file but never see the stored secrets.
- Links carry optional expiration, are revokable any time.

### Added — Platform readiness card (`8bf6a2e`)
- Settings page shows green/amber pills for Anthropic key, SMTP,
  public URL, portal-integration backend.
- Auth-gated; secrets are never echoed back through the API.

## v0.5.0 — Notifications + archive + filters · 2026-05-20

### Added
- **Best-effort SMTP notifications** when an agent queues an
  approval. Empty SMTP config → silent no-op; delivery failures
  never block the agent loop. (`df6ff70`)
- **Client archive zip** — one-click download of `intake.json` +
  `audit.csv` + every uploaded document. (`849db09`)
- **Approval queue filters**: by client and action_type, matching
  the History page. (`1260986`)
- **Workspaces list enrichment**: each client row now shows stage,
  intake-complete badge, open-task count, pending-approval count.
  (`1910fc7`)

## v0.4.0 — Operator UX overhaul · 2026-05-20

### Added
- **Desktop AppShell**: fixed left sidebar (brand, nav, operator
  email, sign-out) + max-w-7xl content area + live
  pending-approval badge on every page. Mobile gets a compact top
  bar. (`53a670c`, `6c90a2f`)
- **Document uploads with content-addressed storage**: multipart
  POST, SHA-256 keyed (identical bytes dedupe on disk), inline
  upload buttons next to every mandated required-doc row,
  `+ Add document` CTA, inline preview modal for image/PDF/text.
  (`09a352c`, `733a551`, `eb792b5`)
- **Document version history**: group by type, latest first, older
  versions in a collapsible sub-list. (`033a7a9`)
- **Approval notes always visible** + reject requires a non-blank
  reason at the schema level. (`cc3c7a8`)
- **Audit CSV export**: per-client and operator-wide. (`ec7af4c`)
- **Settings page**: rename profile + change password (audited;
  secret never logged). (`65fdf73`)
- **Intake wizard**: 5 labeled fieldsets replace the JSON textarea.
  (`4481f39`)

## v0.3.0 — Vault + portal actions · 2026-05-20

### Added
- **Founder-stage onboarding**: a single person can start before
  the entity exists; corporate docs unlock once EIN is captured.
  (`2807696`)
- **Encrypted credential vault**: per-client external logins (FCC,
  state PUCs, carriers). Agents see *which* services exist, never
  the secret. Every decryption audits as `credential.accessed`.
  (`c957241`)
- **Portal-action tool**: vault-gated, T2-tier. Agent picks a
  service + action; platform decrypts and runs the integration
  with the operator's approval. (`5181b1f`)
- **Per-service portal-action catalog**: registry, schema
  validation, prettier cards. (`d6a91c8`, `caa2e5d`)
- **Integration adapter + demo backend**: pluggable layer so
  Playwright / HTTP integrations slot in without touching the
  approval/audit surface. (`f9abcdd`)

## v0.2.0 — Core platform features · 2026-05-20

### Added
- **Approval queue**: review / edit&approve / reject / batch. The
  product. (`1c1a1b3`, `16988a3`)
- **PM orchestration**: workspace-aware tools + sub-task delegation
  through the same tier-gated boundary. (`671ded1`)
- **Execute on approval**: approving an action materializes a
  versioned `Document` and records the result. (`bc9452c`)
- **Bounded bulk sweep**: run all queued sub-tasks for a client,
  capped to bound API spend. (`d2d03cb`)
- **Daily ops briefing**: deterministic operator-wide digest of
  what's captured, what's blocked, what's waiting on you.
  (`c5b27a1`)
- **Auto-decided required documents**: mandate per client based on
  intake (target_states, international intent); `*required` /
  `scan` flags. (`d04152f`)
- **Immutable audit log**: every consequential action, append-only.
  (`ac120d3`)
- **Document request pack PDF**: one tailored, emailable file with
  per-document specs the client can read. (`ab7a5ac`, `3bc19dd`)
- **Action history**: filterable decided-approvals page. (`06041c7`)

## v0.1.0 — Foundation · 2026-05-19

### Added
- Monorepo skeleton (`apps/web` Next.js 14, `apps/api` FastAPI).
- Auth: register, login, JWT. (`e6b19fe`)
- Operator-scoped client workspace CRUD. (`f09f538`)
- Capture-once intake mandate. (`e043689`)
- Agent runtime (`run_agent` manual tool-use loop with tier-gated
  approval boundary). (`181f90b`)
- Three-agent roster: Compliance, Document, PM. (`18cda36`)
- E2E smoke test. (`f7125c5`)
- CORS middleware so the browser can call the API. (`82b4487`)

### Decided
- E-signature provider: **self-hosted Documenso** (not SaaS).
  Integration code is the obvious next phase. (`18303c3`)

---

## How updates flow

Switchboard auto-deploys from `main`. The flow once you've set up
Railway per [DEPLOY.md](docs/DEPLOY.md):

1. **Push to `main`** on GitHub (e.g. via this repo's commits).
2. **Railway picks it up**: rebuilds the affected service (api or
   web) and ships the new image. Watch progress in the Railway
   dashboard → service → Deployments.
3. **API container boots**, runs `alembic upgrade head` first, then
   `uvicorn`. So schema migrations apply automatically.
4. **Web container builds with the new `NEXT_PUBLIC_API_URL`** as
   a build arg and serves the new bundle.

Total: usually 2-4 minutes from push to live.

Roll back: in Railway, service → Deployments → click an earlier
green deploy → "Redeploy". Postgres migrations are *not*
auto-reverted; if a migration broke something, also run
`alembic downgrade -1` manually inside the api container.
