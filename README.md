# Switchboard

The AI Operator Platform for Regulated Launches. One operator supervising a team of
Claude-powered agents to run US VoIP company launches at 10x throughput.

> Confidential. This repository holds regulated client data and credentials — keep it **private**.

## Monorepo layout

```
switchboard/
├── apps/
│   ├── web/        # Next.js 14 dashboard (operator UI, approval queue)
│   └── api/        # FastAPI backend (orchestration, agent runtime)
├── packages/
│   ├── agents/     # Claude Agent SDK — PM, Compliance, Document agents
│   └── shared/     # Shared schemas / types (Pydantic + TS)
├── infra/          # Docker, CI, Terraform (later phases)
└── docs/           # Switchboard.pptx — the investor & engineering brief
```

## Phase 1 (Weeks 1–6) — MVP scope

Client workspaces + auth · Project Manager + Compliance + Document agents ·
Approval queue + Telegram · Claude API + Gmail + DocuSign · run live with 1–2 clients.

## Setup

1. Copy the env template and fill in **test/sandbox** keys:
   ```
   cp .env.example .env
   ```
2. Never commit `.env`. Secrets stay local; production secrets go in Railway env vars.
3. App scaffolding (`apps/web`, `apps/api`) is added in Week 1.

## Security

- No account passwords or SSH private keys are ever shared or committed.
- Use test-mode keys in development; live keys only in the deploy environment.
- `.env`, `*.key`, `*.pem`, and `secrets/` are git-ignored.
