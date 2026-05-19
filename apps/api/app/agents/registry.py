from app.agents.base import AgentSpec
from app.agents.compliance import COMPLIANCE_AGENT
from app.agents.document import DOCUMENT_AGENT
from app.agents.pm import PM_AGENT

# Phase 1 roster: Project Manager + Compliance + Document.
AGENTS: dict[str, AgentSpec] = {
    spec.name: spec for spec in (PM_AGENT, COMPLIANCE_AGENT, DOCUMENT_AGENT)
}


def get_agent(name: str) -> AgentSpec | None:
    return AGENTS.get(name)
