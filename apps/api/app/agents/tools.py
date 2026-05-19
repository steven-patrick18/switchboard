from collections.abc import Callable
from dataclasses import dataclass

from app.models.approval import (
    TIER_APPROVE,
    TIER_AUTO,
    TIER_AUTO_NOTIFY,
    TIER_SIGN_PAY,
)

AUTO_TIERS = {TIER_AUTO, TIER_AUTO_NOTIFY}
GATED_TIERS = {TIER_APPROVE, TIER_SIGN_PAY}


@dataclass(frozen=True)
class Tool:
    """A scoped tool. T0/T1 run inline; T2/T3 are blocked behind the
    approval queue and never execute until an operator approves."""

    name: str
    description: str
    input_schema: dict
    tier: str
    runner: Callable[[dict], str] | None = None

    def to_anthropic(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


# --- Compliance Agent tool implementations (T0 — safe internal reads) ---

_FCC_REFERENCE = {
    "499": (
        "FCC Form 499-A (annual) / 499-Q (quarterly): filer ID registration "
        "for USF/TRS contributions. Filed via the USAC E-File system. Officer "
        "must certify under penalty of perjury."
    ),
    "rmd": (
        "Robocall Mitigation Database: every voice service provider must file "
        "a robocall mitigation plan and certify STIR/SHAKEN status before "
        "carriers may accept its traffic."
    ),
    "stir/shaken": (
        "STIR/SHAKEN: caller-ID authentication. Token issuance requires an "
        "OSP code and live officer vetting via the STI-PA (iconectiv)."
    ),
    "214": (
        "Section 214: authorization for international service. Domestic "
        "non-dominant carriers have blanket 214; international requires an "
        "application to the FCC International Bureau."
    ),
    "cpcn": (
        "State CPCN (Certificate of Public Convenience and Necessity): "
        "per-state authority to provide intrastate service. ~50 distinct "
        "processes; some require notarized/wet-ink signatures and hearings."
    ),
}


def _lookup_fcc_requirement(args: dict) -> str:
    topic = str(args.get("topic", "")).strip().lower()
    for key, text in _FCC_REFERENCE.items():
        if key in topic:
            return text
    return (
        "No specific reference cached for that topic. General rule: confirm "
        "the requirement against the current FCC rule and the relevant state "
        "PUC before drafting any filing."
    )


lookup_fcc_requirement = Tool(
    name="lookup_fcc_requirement",
    description=(
        "Look up a cached regulatory reference for a US VoIP launch topic "
        "(499, RMD, STIR/SHAKEN, Section 214, state CPCN). Read-only."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Topic, e.g. '499', 'RMD', 'STIR/SHAKEN', 'CPCN'.",
            }
        },
        "required": ["topic"],
    },
    tier=TIER_AUTO,
    runner=_lookup_fcc_requirement,
)


queue_filing_submission = Tool(
    name="queue_filing_submission",
    description=(
        "Submit a regulatory filing on the client's behalf. This is a "
        "tier-3 action: it is queued for operator approval and does NOT "
        "execute until a human signs off."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "form": {
                "type": "string",
                "description": "Filing identifier, e.g. 'FCC 499-A'.",
            },
            "summary": {
                "type": "string",
                "description": "Plain-language summary of what will be submitted.",
            },
            "payload": {
                "type": "object",
                "description": "The structured filing data.",
            },
        },
        "required": ["form", "summary"],
    },
    tier=TIER_SIGN_PAY,
    runner=None,
)
