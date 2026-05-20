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
    "cores": (
        "FCC CORES (Commission Registration System): the FCC's master "
        "registration. Every entity that files anything with the FCC "
        "needs an FRN (FCC Registration Number) obtained here. Free, "
        "~15 minutes online at apps.fcc.gov/cores. PREREQUISITE for "
        "Form 499; NECA also asks for the FRN on the OCN application. "
        "ALWAYS the first FCC step."
    ),
    "frn": (
        "FRN (FCC Registration Number): 10-digit ID issued by FCC CORES. "
        "Required on every subsequent FCC filing (499, RMD, 214, etc.)."
    ),
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
    "Codified US VoIP launch (v3):\n\n"
    "PHASE A — ENTITY (intake agent)\n"
    "1. Entity formation, EIN, business bank account — captured from intake.\n\n"
    "PHASE B — THE MANDATORY FIVE (sequenced; later ones depend on earlier)\n"
    "These five MUST be in place before any wholesale carrier will "
    "provision live voice traffic for the client:\n"
    "  1. CORES (FRN) — apps.fcc.gov/cores. Free, ~15 min online. Gets "
    "you the FRN required on every subsequent FCC filing AND on the OCN "
    "application. Owner: compliance agent. ALWAYS FIRST.\n"
    "  2. OCN — Form NECA-OCN-2 at neca.org. Operator applies on the "
    "client's behalf with an LOA. Timeline 2-3 weeks. Required for RMD "
    "and most wholesale carriers. Owner: carrier agent.\n"
    "  3. FCC Form 499-A (and 499-Q quarterly) — USAC E-File. Officer "
    "certifies under penalty of perjury. Owner: compliance agent.\n"
    "  4. Robocall Mitigation Database — efile.fcc.gov. Requires OCN. "
    "Either describes the client's STIR/SHAKEN status OR a mitigation "
    "plan if STIR/SHAKEN is not yet live. Carriers won't accept traffic "
    "from non-RMD providers. Owner: compliance agent.\n"
    "  5. STIR/SHAKEN cert — STI-PA / iconectiv. Requires CORES + OCN + "
    "RMD + officer vetting. Timeline 4-8 weeks. One cert covers both "
    "STIR (verify) and SHAKEN (sign) sides. Owner: carrier agent.\n\n"
    "PHASE C — INTERNATIONAL (only if intake.intends_international)\n"
    "6. FCC Section 214 — application to the International Bureau. Owner: "
    "compliance agent.\n\n"
    "PHASE D — STATE (state_licensing agent)\n"
    "7. State CPCN / SPCOA / Section 99 in each target state. ~50 distinct "
    "processes; some require notarization, surety bonds, hearings.\n\n"
    "PHASE E — CARRIERS (carrier agent + document agent for MSAs)\n"
    "8. Wholesale carrier onboarding (Twilio, Telnyx, Bandwidth, "
    "Inteliquent etc.). Most carriers require OCN + RMD + STIR/SHAKEN "
    "before live numbers; sign at least 1-3 carriers for redundancy.\n\n"
    "PHASE F — GO-LIVE & ONGOING\n"
    "9. Number provisioning, test calls, monitoring.\n"
    "10. Ongoing: 499-Q quarterly, CPNI annual (March 1), Form 477 "
    "semi-annual, state annual reports.\n\n"
    "PM coordinates step ordering; the operator owns deposits, wet-ink "
    "signatures, and final carrier calls. Use assign_task to delegate, "
    "and update_application_stage to self-report progress on each filing."
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
    "ocn": (
        "OCN (Operating Company Number): issued by NECA, not the FCC. "
        "Submit Form NECA-OCN-2 at neca.org/business-solutions/"
        "companycodeocnadministration. Operator applies on behalf of the "
        "client with a Letter of Agency. Timeline 2-3 weeks. "
        "PREREQUISITE for: RMD entry, STIR/SHAKEN, and most wholesale "
        "carriers (Bandwidth especially). Do not start RMD or carrier "
        "onboarding without OCN in hand."
    ),
    "neca": (
        "NECA (National Exchange Carrier Association) administers OCN "
        "assignment + ACNA codes. The OCN application is the gate to "
        "almost every downstream telecom step; queue it first."
    ),
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


# --- Application status reporting (workspace-aware, T1) -------------


async def _update_application_stage(args: dict, ctx: ToolContext) -> str:
    """Agent self-reports progress on a specific filing. The agent picks
    the application by type (e.g. 'fcc_499', 'state_cpcn:TX', 'ocn')
    and updates its stage + a short note. Stages are coarse on purpose:
    not_started, in_progress, awaiting_approval, submitted, under_review,
    complete, blocked. The current_agent field is set to whichever agent
    called the tool, so the UI shows 'compliance is working on FCC 499'
    in real time."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.models import Application  # noqa: PLC0415
    from app.models.application import ALL_STAGES  # noqa: PLC0415

    if ctx.client_id is None:
        return "No client context — cannot update application status."
    app_type = str(args.get("application_type", "")).strip()
    stage = str(args.get("stage", "")).strip()
    note = str(args.get("note", "")).strip() or None
    if not app_type:
        return "Missing application_type."
    if stage and stage not in ALL_STAGES:
        return f"Invalid stage '{stage}'. Valid: {sorted(ALL_STAGES)}."

    row = await ctx.db.scalar(
        select(Application).where(
            Application.client_id == ctx.client_id,
            Application.type == app_type,
        )
    )
    if row is None:
        return (
            f"No application of type '{app_type}' exists for this client. "
            "Operator must add it first (or run sync-from-intake)."
        )
    if stage:
        row.stage = stage
    if note is not None:
        row.notes = note
    row.current_agent = ctx.agent_name
    return (
        f"Updated {app_type}: stage={row.stage}, current_agent="
        f"{row.current_agent}, note={'set' if note else 'unchanged'}."
    )


update_application_stage = Tool(
    name="update_application_stage",
    description=(
        "Self-report your progress on a specific filing/application "
        "(OCN, FCC 499, RMD, STIR/SHAKEN, state_cpcn:XX, carrier:Y). "
        "Sets the application's stage and an optional note, and marks "
        "yourself as the current_agent so the operator's dashboard "
        "shows who is working on what. Tier-1: auto-runs but audited."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "application_type": {
                "type": "string",
                "description": (
                    "Exact application type code: 'ocn', 'fcc_499', "
                    "'rmd', 'stir_shaken', 'section_214', "
                    "'state_cpcn:TX', 'carrier:bandwidth', etc."
                ),
            },
            "stage": {
                "type": "string",
                "description": (
                    "One of: not_started, in_progress, awaiting_approval, "
                    "submitted, under_review, complete, blocked. Leave "
                    "unchanged by omitting."
                ),
            },
            "note": {
                "type": "string",
                "description": (
                    "Short note explaining current state — e.g. "
                    "'Waiting on operator to sign LOA before NECA submit'."
                ),
            },
        },
        "required": ["application_type"],
    },
    tier=TIER_AUTO_NOTIFY,
    db_runner=_update_application_stage,
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
        # Status reporting — every agent should use this to mark
        # progress on its current application.
        update_application_stage,
    )
}


def get_tool(name: str) -> Tool | None:
    return ALL_TOOLS.get(name)
