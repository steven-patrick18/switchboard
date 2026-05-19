# apps/api

FastAPI backend — orchestration layer, approval gateway, agent runtime host.

Stack: Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.x (async) · Alembic

## Local development

1. Start Postgres + Redis:
   ```
   docker compose -f ../../infra/docker-compose.yml up -d
   ```
2. Create a virtualenv and install deps:
   ```
   python -m venv .venv
   .venv\Scripts\activate        # Windows
   pip install -e ".[dev]"
   ```
3. Apply database migrations:
   ```
   alembic upgrade head
   ```
4. Run the API:
   ```
   uvicorn app.main:app --reload
   ```
5. Check it:
   - http://localhost:8000/health
   - http://localhost:8000/health/db  (verifies Postgres)
   - http://localhost:8000/docs       (OpenAPI)

Config is read from the repo-root `.env` (see `.env.example`). The default
`DATABASE_URL` matches the Docker Compose Postgres credentials.

## Agents

`app/agents/` holds the Claude Agent runtime. Phase 1: the Compliance
Agent. Every tool is tier-classified — T0/T1 run inline, **T2/T3 are
intercepted and queued to the approval table; they never execute until an
operator approves**. Run it via `POST /agents/compliance/run`
(`{client_id, instruction}`), which needs `ANTHROPIC_API_KEY` set.

## Tests

```
pytest
```

`tests/test_agent_gateway.py` verifies the approval gateway with a fake
Anthropic client — no API spend. `scripts/smoke.py` is the HTTP smoke test.
