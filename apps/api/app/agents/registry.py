from app.agents.base import AgentSpec
from app.agents.carrier import CARRIER_AGENT
from app.agents.compliance import COMPLIANCE_AGENT
from app.agents.document import DOCUMENT_AGENT
from app.agents.intake import INTAKE_AGENT
from app.agents.pm import PM_AGENT
from app.agents.readiness import READINESS_AGENT
from app.agents.state_licensing import STATE_LICENSING_AGENT

# Seven-agent roster, distributed by scope so each agent has a clean
# domain and doesn't step on the others:
#   pm              — orchestrator (delegates; no external action)
#   readiness       — read-only "what can we start now?" advisor
#   intake          — client data + capture-once mandate
#   compliance      — federal FCC filings (CORES, 499, RMD, 214)
#   state_licensing — per-state CPCNs / PUC paperwork
#   carrier         — wholesale interconnection, OCN, STIR/SHAKEN
#   document        — drafts and signing
AGENTS: dict[str, AgentSpec] = {
    spec.name: spec
    for spec in (
        PM_AGENT,
        READINESS_AGENT,
        INTAKE_AGENT,
        COMPLIANCE_AGENT,
        STATE_LICENSING_AGENT,
        CARRIER_AGENT,
        DOCUMENT_AGENT,
    )
}


def get_agent(name: str) -> AgentSpec | None:
    return AGENTS.get(name)
