from fastapi import HTTPException, status

from app.config import settings


def get_anthropic_client():
    """FastAPI dependency. Injectable so tests can override it with a fake
    (no API spend). Raises 503 when the key isn't configured."""
    if not settings.anthropic_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ANTHROPIC_API_KEY is not configured.",
        )
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(api_key=settings.anthropic_api_key)
