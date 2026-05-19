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


# --- Project Manager Agent tools (T0) ---

_VOIP_LAUNCH_PLAYBOOK = (
    "Codified US VoIP launch (v1):\n"
    "1. Entity & identity — confirm legal entity, EIN, officers, principal "
    "address (from client intake).\n"
    "2. FCC layer — Form 499-A/Q filer registration, RMD entry, "
    "STIR/SHAKEN token via STI-PA, Section 214 if international.\n"
    "3. State layer — CPCN/registration in each target state.\n"
    "4. Carrier onboarding — wholesale applications, credit/KYC, MSAs, "
    "deposits with 1-3 carriers.\n"
    "5. Go-live — number provisioning, test calls, monitoring.\n"
    "6. Ongoing — 499-Q quarterly, CPNI, USF, annual filings.\n"
    "Owners: Compliance owns 2-3; Document owns MSAs/LOAs/ToS/AUP; the "
    "operator owns deposits, signatures, and carrier calls."
)


def _get_voip_launch_playbook(_args: dict) -> str:
    return _VOIP_LAUNCH_PLAYBOOK


get_voip_launch_playbook = Tool(
    name="get_voip_launch_playbook",
    description=(
        "Return the codified, phased US VoIP launch checklist used to "
        "decompose a launch into sequenced, owner-assigned steps. Read-only."
    ),
    input_schema={"type": "object", "properties": {}},
    tier=TIER_AUTO,
    runner=_get_voip_launch_playbook,
)


# --- Document Agent tools ---

_DOC_TEMPLATES = {
    "tos": (
        "Terms of Service outline: parties; service description; acceptable "
        "use ref; fees/billing; SLAs; limitation of liability; indemnity; "
        "termination; governing law; CPNI/privacy ref."
    ),
    "aup": (
        "Acceptable Use Policy outline: prohibited traffic (robocalls, "
        "spoofing, fraud); STIR/SHAKEN attestation duties; traffic-pumping "
        "ban; suspension rights; reporting obligations."
    ),
    "loa": (
        "Letter of Authorization outline: authorizing entity, authorized "
        "party, scope (number port / carrier provisioning), effective "
        "dates, officer signature block."
    ),
    "msa_review": (
        "Carrier MSA review checklist: term/renewal, deposit & true-up, "
        "rate change notice, MOU commit/shortfall, traffic quality / "
        "blocking, indemnity, termination & data return — flag any "
        "one-sided clause for the operator."
    ),
}


def _lookup_document_template(args: dict) -> str:
    key = str(args.get("doc_type", "")).strip().lower()
    return _DOC_TEMPLATES.get(
        key,
        "No cached template. Supported: tos, aup, loa, msa_review. Draft "
        "conservatively and flag novel clauses for operator review.",
    )


lookup_document_template = Tool(
    name="lookup_document_template",
    description=(
        "Return a cached outline/checklist for a legal document type "
        "(tos, aup, loa, msa_review). Read-only."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "doc_type": {
                "type": "string",
                "description": "One of: tos, aup, loa, msa_review.",
            }
        },
        "required": ["doc_type"],
    },
    tier=TIER_AUTO,
    runner=_lookup_document_template,
)


send_document_for_signature = Tool(
    name="send_document_for_signature",
    description=(
        "Send a drafted document to a client or carrier for e-signature. "
        "Tier-3: queued for operator approval and does NOT send until a "
        "human signs off."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "doc_type": {"type": "string", "description": "e.g. 'LOA', 'MSA'."},
            "recipient": {
                "type": "string",
                "description": "Signer email / party.",
            },
            "summary": {
                "type": "string",
                "description": "What is being sent and why.",
            },
        },
        "required": ["doc_type", "recipient", "summary"],
    },
    tier=TIER_SIGN_PAY,
    runner=None,
)
