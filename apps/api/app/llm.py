from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import platform_config
from app.db import get_db


async def get_anthropic_client(db: AsyncSession = Depends(get_db)):
    """FastAPI dependency. Reads the key from the platform_settings
    table first (GUI-editable), falling back to the static env config
    so existing .env-based deploys keep working. Injectable so tests
    can override it with a fake (no API spend). Raises 503 when the
    key isn't configured in either place."""
    key = await platform_config.anthropic_api_key(db)
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "ANTHROPIC_API_KEY is not configured. Open Settings → "
                "Platform readiness to set it."
            ),
        )
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(api_key=key)
