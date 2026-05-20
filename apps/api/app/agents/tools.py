import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.intake import evaluate as evaluate_intake
from app.portal_actions import services as _portal_services_list
from app.models.approval import (
    TIER_APPROVE,
    TIER_AUTO,
    TIER_AUTO_NOTIFY,
    TIER_SIGN_PAY,
)
from app.models.client_intake import ClientIntake
from app.models.document import Document
from app.models.task import Task

AUTO_TIERS = {TIER_AUTO, TIER_AUTO_NOTIFY}
GATED_TIERS = {TIER_APPROVE, TIER_SIGN_PAY}


@dataclass(frozen=True)
class ToolContext:
    """Workspace context handed to db-aware tools so they can read/write
    the client's records. Pure stateless tools never receive this."""

    db: AsyncSession
    client_id: uuid.UUID | None
    project_id: uuid.UUID | None
    task_id: uuid.UUID
    agent_name: str = "agent"


@dataclass(frozen=True)
class Tool:
    """A scoped tool. T0/T1 run inline; T2/T3 are blocked behind the
    approval queue and never execute until an operator approves.

    `runner` is a pure function of the args. `db_runner` is for
    workspace-aware tools that read/write the client's records — it is
    async and receives a ToolContext. A tool sets at most one."""

    name: str
    description: str
    input_schema: dict
    tier: str
    runner: Callable[[dict], str] | None = None
    db_runner: Callable[[dict, ToolContext], Awaitable[str]] | None = None

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


# --- Project Manager orchestration tools (workspace-aware) ---

_ASSIGNABLE = {"compliance", "document", "intake", "state_licensing", "carrier"}


async def _check_intake_status(_args: dict, ctx: ToolContext) -> str:
    if ctx.client_id is None:
        return "No client context available."
    intake = await ctx.db.scalar(
        select(ClientIntake).where(ClientIntake.client_id == ctx.client_id)
    )
    doc_types = set(
        (
            await ctx.db.scalars(
                select(Document.type).where(Document.client_id == ctx.client_id)
            )
        ).all()
    )
    c = evaluate_intake(intake, doc_types)
    if c.complete:
        return "Client intake is COMPLETE — all required data and documents captured."
    missing = []
    for d in c.required_documents:
        if not d.provided:
            tag = " (needs scan)" if d.needs_scan else ""
            missing.append(f"{d.key}{tag}")
    return (
        "Client intake is INCOMPLETE. Missing fields: "
        + (", ".join(c.missing_fields) or "none")
        + ". Missing mandated documents: "
        + (", ".join(missing) or "none")
        + ". Documents marked '(needs scan)' must be a scanned physical "
        "copy. Blocked steps cannot start until these are captured."
    )


check_intake_status = Tool(
    name="check_intake_status",
    description=(
        "Read the client's capture-once intake status: whether every "
        "required datum and document is captured, and what is missing. "
        "Read-only. Use this before planning to find blockers."
    ),
    input_schema={"type": "object", "properties": {}},
    tier=TIER_AUTO,
    db_runner=_check_intake_status,
)


async def _assign_task(args: dict, ctx: ToolContext) -> str:
    agent = str(args.get("agent", "")).strip().lower()
    objective = str(args.get("objective", "")).strip()
    if agent not in _ASSIGNABLE:
        return (
            f"Cannot assign to '{agent}'. Valid targets: "
            f"{', '.join(sorted(_ASSIGNABLE))}."
        )
    if not objective:
        return "An objective is required to assign a task."
    if ctx.project_id is None:
        return "No project context; cannot assign a task."
    task = Task(
        project_id=ctx.project_id,
        agent=agent,
        status="queued",
        input={"objective": objective, "assigned_by": "pm"},
    )
    ctx.db.add(task)
    await ctx.db.flush()
    return (
        f"Assigned {agent} a queued task ({task.id}): {objective}. It will "
        f"run later through its own scoped tools and approval gateway."
    )


assign_task = Tool(
    name="assign_task",
    description=(
        "Delegate a launch step by creating a queued task for another "
        "agent. Tier-1: internal orchestration only — it queues work, "
        "it does NOT execute it or take any external/regulated action. "
        "Pick the most specific agent for the job (intake for client "
        "data, compliance for FCC, state_licensing for state PUC, "
        "carrier for wholesale interconnection, document for drafts)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": (
                    "Target agent name: one of 'intake', 'compliance', "
                    "'state_licensing', 'carrier', 'document'."
                ),
            },
            "objective": {
                "type": "string",
                "description": "What that agent should accomplish.",
            },
        },
        "required": ["agent", "objective"],
    },
    tier=TIER_AUTO_NOTIFY,
    db_runner=_assign_task,
)


# --- Credential vault (availability only — never the secret) ---


async def _check_client_credentials(_args: dict, ctx: ToolContext) -> str:
    if ctx.client_id is None:
        return "No client context available."
    from app.vault import credential_availability

    rows = await credential_availability(
        ctx.db, ctx.client_id, actor=ctx.agent_name
    )
    if not rows:
        return (
            "No external login credentials are on file for this client. "
            "The operator must add them (e.g. FCC CORES, IRS, state PUC, "
            "bank, carrier portals) before those steps can be actioned."
        )
    parts = []
    for r in rows:
        state = "EXPIRED" if r["expired"] else "available"
        parts.append(f"{r['service']} ({state})")
    return (
        "Credentials on file (you cannot see the secrets — the platform "
        "uses them on your behalf, and this lookup was audited): "
        + ", ".join(parts)
        + ". Plan around missing/expired services."
    )


check_client_credentials = Tool(
    name="check_client_credentials",
    description=(
        "List which external services have a login credential on file "
        "for this client (and whether expired). Never returns secrets — "
        "the platform uses them server-side. Read-only; the lookup is "
        "audited."
    ),
    input_schema={"type": "object", "properties": {}},
    tier=TIER_AUTO,
    db_runner=_check_client_credentials,
)


request_portal_action = Tool(
    name="request_portal_action",
    description=(
        "Request a credential-backed action on an external service "
        "(FCC CORES, state PUC, IRS, carrier portal, bank). Tier-2: it "
        "queues for operator approval. On approval the platform uses the "
        "stored credential server-side to perform the action — the agent "
        "never sees the secret. Never claim the action was done; only "
        "that it was queued."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "service": {
                "type": "string",
                "enum": _portal_services_list(),
                "description": "Credential key — pick from the supported services.",
            },
            "action": {
                "type": "string",
                "description": (
                    "Known actions are listed in the portal-action catalog "
                    "(e.g. 'check_filer_status', 'submit_499_q', "
                    "'check_cpcn_status'). Use one of those when applicable."
                ),
            },
            "summary": {
                "type": "string",
                "description": "Plain-language summary the operator will see.",
            },
            "params": {
                "type": "object",
                "description": (
                    "Action-specific parameters (no secrets). Required keys "
                    "depend on the action — see the catalog."
                ),
            },
        },
        "required": ["service", "action", "summary"],
    },
    tier=TIER_APPROVE,
    runner=None,
)


# --- State Licensing scope (T0 reference lookups) -------------------

_STATE_REFERENCE = {
    "tx": (
        "Texas: PUC of Texas — Service Provider Certificate of Operating "
        "Authority (SPCOA). Online filing via Interchange. Officer "
        "certification + bond may apply for certain CLEC classes."
    ),
    "ca": (
        "California: CPUC — Certificate of Public Convenience and Necessity "
        "(CPCN). Application + financial showing + officer character "
        "qualifications. Public-comment/hearing common; multi-month timeline."
    ),
    "ny": (
        "New York: NY PSC — Section 99 certificate for resale; full CPCN "
        "for facilities-based. Background check on principals; tariff filing."
    ),
    "fl": (
        "Florida: FPSC — Certificate of registration for IXC/local; minimal "
        "tariff but annual regulatory assessment fees apply."
    ),
    "il": (
        "Illinois: ICC — Certificate of Service Authority for non-incumbent "
        "telecoms. Application + bond; relatively streamlined process."
    ),
}


def _lookup_state_requirement(args: dict) -> str:
    state = str(args.get("state", "")).strip().lower()
    text = _STATE_REFERENCE.get(state)
    if text is not None:
        return text
    return (
        f"No specific reference cached for state '{state}'. General rule: "
        "every state PUC has its own process; confirm with the live PUC site "
        "(filings page) before drafting the CPCN application. Notarization "
        "and a surety bond are common requirements."
    )


lookup_state_requirement = Tool(
    name="lookup_state_requirement",
    description=(
        "Look up cached state-level CPCN / PUC licensing requirements by "
        "2-letter state code (TX, CA, NY, FL, IL, ...). Read-only."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "state": {
                "type": "string",
                "description": "2-letter US state code (uppercase or lowercase).",
            }
        },
        "required": ["state"],
    },
    tier=TIER_AUTO,
    runner=_lookup_state_requirement,
)


# --- Carrier scope (T0 reference lookups) ---------------------------

_CARRIER_REFERENCE = {
    "twilio": (
        "Twilio Programmable Voice / SIP Trunking: Service Profile required "
        "for messaging; phone-number subaccount for voice. SIP trunk needs "
        "ACL + credentials; supports SHAKEN signing for owned numbers."
    ),
    "telnyx": (
        "Telnyx Mission Control: BYOC trunking with full PASSporT signing. "
        "Per-second billing; requires regulatory bundle (entity docs + "
        "officer ID) for E911 and STIR/SHAKEN certificate issuance."
    ),
    "bandwidth": (
        "Bandwidth Dashboard: tier-1 carrier with E911, messaging, voice. "
        "OCN-based interconnection (need OCN issued by NECA first). LOA "
        "required for porting; financial review before live."
    ),
    "inteliquent": (
        "Inteliquent / Sinch Voice: wholesale interconnection. NOF (notice "
        "of facilities) for direct interconnect. STI-PA-issued cert needed "
        "for outbound SHAKEN signing across their network."
    ),
    "stir/shaken": (
        "STIR/SHAKEN: token issuance requires (a) FCC-registered OCN, (b) "
        "completed Robocall Mitigation Database entry, (c) STI-PA officer "
        "vetting (iconectiv) — typically 4-8 weeks end to end."
    ),
}


def _lookup_carrier_specs(args: dict) -> str:
    carrier = str(args.get("carrier", "")).strip().lower()
    for key, text in _CARRIER_REFERENCE.items():
        if key in carrier:
            return text
    return (
        f"No specific spec cached for carrier '{carrier}'. General rule: "
        "wholesale carriers need OCN + RMD + officer vetting before they "
        "will provision live numbers. Ask the carrier's regulatory team "
        "for their current onboarding bundle."
    )


lookup_carrier_specs = Tool(
    name="lookup_carrier_specs",
    description=(
        "Look up cached interconnection / regulatory specs for a wholesale "
        "carrier (Twilio, Telnyx, Bandwidth, Inteliquent) or 'STIR/SHAKEN'. "
        "Read-only."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "carrier": {
                "type": "string",
                "description": "Carrier name, lowercase, e.g. 'twilio'.",
            }
        },
        "required": ["carrier"],
    },
    tier=TIER_AUTO,
    runner=_lookup_carrier_specs,
)


# --- Client communication (T2: requires operator approval) ----------

draft_client_email = Tool(
    name="draft_client_email",
    description=(
        "Draft an email to send to the client (subject + body + recipient "
        "role). Tier-2: queued for operator approval; the platform does "
        "NOT send anything externally until a human approves."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "recipient_role": {
                "type": "string",
                "description": "Who on the client side, e.g. 'officer', 'primary_contact'.",
            },
            "subject": {
                "type": "string",
                "description": "Email subject line.",
            },
            "body": {
                "type": "string",
                "description": "Plain-language email body (no merge tokens).",
            },
            "purpose": {
                "type": "string",
                "description": (
                    "Why this email exists, e.g. 'request EIN letter', "
                    "'request notarized state CPCN signature'."
                ),
            },
        },
        "required": ["recipient_role", "subject", "body", "purpose"],
    },
    tier=TIER_APPROVE,
    runner=None,
)


# --- Tool catalog (single source of truth for the GUI picker) -------
# Every code-defined Tool that an operator can include in an agent
# must be registered here. Tiers stay code-defined for safety; the
# operator can only choose which tools an agent gets, not redefine
# them or change their tier classification.

ALL_TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        # Compliance scope (FCC)
        lookup_fcc_requirement,
        queue_filing_submission,
        # State Licensing scope
        lookup_state_requirement,
        # Carrier scope
        lookup_carrier_specs,
        # Project-management scope
        get_voip_launch_playbook,
        check_intake_status,
        assign_task,
        # Intake / client communication
        draft_client_email,
        # Document scope
        lookup_document_template,
        send_document_for_signature,
        # Cross-cutting
        check_client_credentials,
        request_portal_action,
    )
}


def get_tool(name: str) -> Tool | None:
    return ALL_TOOLS.get(name)
