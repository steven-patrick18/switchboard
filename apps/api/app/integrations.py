"""Portal-integration adapter — the seam between the platform and real
external portals (FCC CORES, state PUCs, carriers).

v1 ships a deterministic `demo` backend that simulates a successful
portal interaction without external I/O — safe for dev/CI and enough
to wire the entire flow end-to-end (vault decrypt + audit + adapter
call + Document Hub artifact). A real Playwright/HTTP backend is
plugged in alongside this in a follow-up phase and selected via
settings.portal_integration_backend.

The adapter contract is intentionally minimal: handlers receive the
decrypted credential secret and the action params (no model context,
no logging) and return a structured result. Secrets are never put
into IntegrationResult; only status/detail relevant to the operator.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class IntegrationResult:
    status: str  # short status string for the operator
    detail: dict  # structured payload (never contains the secret)
    backend: str  # "demo" | "playwright" | ...


Handler = Callable[[str, dict], Awaitable[IntegrationResult]]


async def _demo_fcc_check_filer_status(
    _secret: str, params: dict
) -> IntegrationResult:
    filer_id = str(params.get("filer_id") or "?")
    return IntegrationResult(
        status="ACTIVE",
        detail={
            "filer_id": filer_id,
            "filer_name": f"Demo Filer {filer_id}",
            "form_499_status": "Current",
            "last_filed": "2026-04-15",
        },
        backend="demo",
    )


_DEMO_HANDLERS: dict[tuple[str, str], Handler] = {
    ("fcc_cores", "check_filer_status"): _demo_fcc_check_filer_status,
}


def get_handler(service: str, action: str) -> Handler | None:
    backend = settings.portal_integration_backend
    if backend == "demo":
        return _DEMO_HANDLERS.get((service, action))
    # Real Playwright backend lives in a follow-up phase. Returning None
    # keeps the executor on the existing "recorded; integration layer
    # not yet wired" path until that's in.
    return None
