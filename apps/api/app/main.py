from fastapi import FastAPI
from sqlalchemy import text

from app.api.routes import agents, auth, clients, intake
from app.config import settings
from app.db import engine

app = FastAPI(title="Switchboard API", version="0.1.0")

app.include_router(auth.router)
app.include_router(clients.router)
app.include_router(intake.router)
app.include_router(agents.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}


@app.get("/health/db")
async def health_db() -> dict[str, str]:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok", "database": "reachable"}
