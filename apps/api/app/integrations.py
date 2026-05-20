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


async def _demo_fcc_lookup_frn(_secret: str, params: dict) -> IntegrationResult:
    """Deterministic synthetic FRN for the demo backend. The real
    Playwright backend will scrape CORES with the same shape — every
    consumer (carrier agent, executor, audit) sees the same fields
    regardless of which backend is live."""
    legal_name = str(params.get("legal_name") or "").strip()
    ein = str(params.get("ein") or "").strip()
    if not (legal_name and ein):
        return IntegrationResult(
            status="MISSING_PARAMS",
            detail={"missing": [k for k in ("legal_name", "ein") if not params.get(k)]},
            backend="demo",
        )
    # Synthetic but deterministic so re-runs are idempotent: zero-pad
    # the last digits of EIN into a 10-digit FRN. Real backend will
    # replace this with a CORES query.
    digits = "".join(c for c in ein if c.isdigit())
    frn = ("0" * 10 + digits)[-10:]
    return IntegrationResult(
        status="FOUND",
        detail={
            "frn": frn,
            "legal_name": legal_name,
            "ein": ein,
            "cores_status": "ACTIVE",
            "note": (
                "Demo backend: synthetic FRN derived from EIN. The real "
                "CORES integration will replace this with the live "
                "registered FRN."
            ),
        },
        backend="demo",
    )


_DEMO_HANDLERS: dict[tuple[str, str], Handler] = {
    ("fcc_cores", "check_filer_status"): _demo_fcc_check_filer_status,
    ("fcc_cores", "lookup_frn"): _demo_fcc_lookup_frn,
}


def get_handler(service: str, action: str) -> Handler | None:
    backend = settings.portal_integration_backend
    if backend == "demo":
        return _DEMO_HANDLERS.get((service, action))
    # Real Playwright backend lives in a follow-up phase. Returning None
    # keeps the executor on the existing "recorded; integration layer
    # not yet wired" path until that's in.
    return None
