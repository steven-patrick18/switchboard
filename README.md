# Switchboard

The AI Operator Platform for Regulated Launches. One operator supervising a team of
Claude-powered agents to run US VoIP company launches at 10x throughput.

> Confidential. This repository holds regulated client data and credentials — keep it **private**.

For architecture, commands, and conventions see **[CLAUDE.md](CLAUDE.md)**. Product brief: `docs/Switchboard.pptx`.

## What works today

End-to-end, all operator-scoped and audited:

- **Auth + client workspaces** (isolated per operator).
- **Capture-once intake** — required fields and the **mandated document set are auto-decided per client** from the intake (`*` mandatory, `scan` where a physical/notarized copy is needed); submit is blocked until complete.
- **Document request pack** — one tailored, client-ready **PDF** (plus per-document specs) to send the client.
- **Agent roster** — `pm` (orchestrates: reads intake, delegates sub-tasks), `compliance`, `document`. Manual Claude tool-use loop with a **tier-gated approval boundary**: T0/T1 run inline; T2/T3 are queued and never execute until a human approves.
- **Approval queue** — review / edit&approve / reject / batch; approving runs the in-platform follow-through (materializes a versioned Document) — real FCC/Documenso I/O is a later integration layer.
- **Task running** — run a queued sub-task, or a bounded bulk sweep of all queued tasks.
- **Immutable audit trail** + a deterministic operator **daily briefing**.
- **Web UI** (`/dashboard`, `/workspaces`, `/clients/[id]`, `/approvals`).

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
