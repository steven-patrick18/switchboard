# Switchboard

The AI Operator Platform for Regulated Launches. One operator supervising a team of
Claude-powered agents to run US VoIP company launches at 10x throughput.

> Confidential. This repository holds regulated client data and credentials — keep it **private**.

For architecture, commands, and conventions see **[CLAUDE.md](CLAUDE.md)**. Product brief: `docs/Switchboard.pptx`.

## What works today

End-to-end, all operator-scoped and audited:

- **Auth + client workspaces** (isolated per operator) + a **Settings** page to rename the operator and change password (audited as `account.password_changed`).
- **Capture-once intake (structured form)** — required fields and the **mandated document set are auto-decided per client** from the intake (`*` mandatory, `scan` where a physical/notarized copy is needed); submit is blocked until complete. Founder-first staging: a single founder gives personal docs before the entity exists; corporate docs become mandated once the EIN is captured.
- **Document hub with real uploads** — multipart upload (PDF, image, scan); content-addressed local storage (SHA-256, automatic dedupe). Per-row "+ upload" / "replace ↑" buttons next to every mandated document; inline preview modal for image/PDF/text; per-file version history; "Download request pack" PDF tailored to the client.
- **Encrypted credentials vault** — store carrier/FCC/state portal logins; agents can see *which* services are on file (audited) but never the secret.
- **Agent roster** — `pm` (orchestrates: reads intake, delegates sub-tasks), `compliance`, `document`. Manual Claude tool-use loop with a **tier-gated approval boundary**: T0/T1 run inline; T2/T3 are queued and never execute until a human approves. Per-service portal-action catalog + adapter pattern (demo backend wired today; Playwright/HTTP integrations slot in).
- **Approval queue** — review / edit&approve / reject / batch with always-visible operator note. Rejection requires a non-blank reason at the schema level so the audit trail always explains why something didn't happen. A live **pending-approval badge in the sidebar** keeps the operator aware from any page.
- **Task running** — run a queued sub-task, or a bounded bulk sweep of all queued tasks.
- **Immutable audit trail** + **CSV export** (per-client and operator-wide) + a deterministic operator **daily briefing** + **per-client archive zip** (intake.json + audit.csv + latest version of every uploaded document) for handoff or compliance archiving.
- **Best-effort SMTP notifications**: configure `SMTP_*` env vars and the platform emails the owning operator the moment an agent queues a tier-2/3 approval. Empty config = email disabled; SMTP failures never block the agent loop.
- **Web UI** with a professional desktop layout (fixed left sidebar, `max-w-7xl` content): `/dashboard`, `/workspaces`, `/clients/[id]`, `/approvals`, `/history`, `/settings`.

## Monorepo layout

```
switchboard/
├── apps/
│   ├── web/        # Next.js 14 operator dashboard
│   └── api/        # FastAPI backend + Claude agent runtime (incl. app/agents/*)
├── packages/       # future home if agent runtime is extracted (Phase 1 lives in apps/api)
├── infra/          # docker-compose (Postgres/Redis) for the optional Postgres path
└── docs/           # Switchboard.pptx — the investor & engineering brief
```

## Quickstart (local, no Docker)

Backend uses SQLite locally; see [CLAUDE.md](CLAUDE.md) for exact commands. In short: install API deps, `alembic upgrade head` against a SQLite `DATABASE_URL`, run `uvicorn app.main:app`, then `npm run dev` in `apps/web`. Agent runs need `ANTHROPIC_API_KEY` in a repo-root `.env`; everything else works keyless.

Copy `.env.example` → `.env` for config. Never commit `.env`. E-signature provider is self-hosted **Documenso**.

## Security

- Operator-scoped data access throughout; no account passwords or SSH private keys are shared or committed.
- Use test-mode keys in development; live keys only in the deploy environment.
- `.env`, `*.key`, `*.pem`, `secrets/`, `*.db` are git-ignored.
