# packages/agents

Claude Agent SDK agents. Phase 1: Project Manager, Compliance, Document.
Each agent runs under a scoped tool whitelist — no agent can spend money,
send external email, or sign without approval-queue approval.

> Phase 1/v0 implementation lives in `apps/api/app/agents/` (shares the
> API venv and DB session). This package is the future home if the agent
> runtime is extracted into a standalone service in a later phase.
