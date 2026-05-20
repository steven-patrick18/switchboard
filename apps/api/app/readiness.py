"""Launch readiness: deterministic 'what can we start right now?'

Given a client's intake + the current state of their `applications`
rows, classify every regulated filing into one of:

  ready     — nothing's blocking it; click Start to kick off the
              right agent with a pre-filled instruction.
  blocked   — prerequisites haven't completed yet (e.g. CORES must
              be done before OCN).
  in_flight — an agent is working on it, or it's already in the
              operator's approval queue / submitted / under review.
  complete  — already done.

Pure Python; no LLM cost. The 7th built-in agent (`readiness`) wraps
this via `summarize_launch_status` so the operator can ask it for a
plain-English summary too.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.applications import label_for, spec_for
from app.intake import evaluate as evaluate_intake
from app.models import Application, ClientIntake, Document
from app.models.application import (
    STAGE_COMPLETE,
    STAGE_NOT_STARTED,
)

# Stages we treat as "the agent / operator is on it" — distinct from
# "ready to kick off" and "fully done".
_IN_FLIGHT_STAGES = {
    "in_progress",
    "awaiting_approval",
    "submitted",
    "under_review",
    "blocked",
}
_DONE = {STAGE_COMPLETE}

# Per-application prereqs. Empty list = no dependency. The Start
# button is enabled only when every prereq is in _DONE.
PREREQS: dict[str, list[str]] = {
    # Founder / entity setup — operator marks these complete manually
    # since they happen outside the platform (LLC filing, IRS, bank).
    "entity_formation": [],
    "ein": ["entity_formation"],
    "bank_account": ["ein"],
    # The mandatory five FCC/NECA filings, sequenced.
    "cores_frn": [],
    "ocn": ["cores_frn"],
    "fcc_499": ["cores_frn"],
    "rmd": ["ocn"],
    "stir_shaken": ["ocn", "rmd"],
    # International.
    "section_214": ["fcc_499"],
    # State CPCNs — independent; can run in parallel once intake is captured.
    # Carrier interconnects — gated on OCN + STIR/SHAKEN.
}


@dataclass(frozen=True)
class StartTemplate:
    """How the GUI's Start button kicks the agent off."""

    agent: str        # which built-in agent picks this up
    instruction: str  # what to say to it


# Per-application: which agent owns it + the templated instruction.
# For state_cpcn:XX and carrier:NAME we generate the template dynamically.
_TEMPLATES: dict[str, StartTemplate] = {
    "cores_frn": StartTemplate(
        agent="compliance",
        instruction=(
            "Draft the FCC CORES (FRN) registration for this client. Use "
            "the captured intake (legal entity name, officer name + title, "
            "officer email, principal address). The officer registers the "
            "FRN at apps.fcc.gov/cores. Queue the action for operator "
            "approval; record the FRN in the application's external_ref "
            "once issued."
        ),
    ),
    "ocn": StartTemplate(
        agent="carrier",
        instruction=(
            "Draft the NECA-OCN-2 application + Letter of Agency for this "
            "client. The FRN is a prerequisite — read it from the "
            "cores_frn application's external_ref. Lay out the document "
            "package the operator needs to mail / upload to NECA. Queue "
            "the action for operator approval."
        ),
    ),
    "fcc_499": StartTemplate(
        agent="compliance",
        instruction=(
            "Draft FCC Form 499-A for this client. Pull legal name, EIN, "
            "officer info, estimated monthly revenue from intake. Use the "
            "FRN from cores_frn.external_ref. Queue for operator approval."
        ),
    ),
    "rmd": StartTemplate(
        agent="compliance",
        instruction=(
            "Draft the Robocall Mitigation Database entry. Reference the "
            "client's OCN. Describe a mitigation plan; if STIR/SHAKEN is "
            "not yet live, file a known-mitigation-techniques plan."
        ),
    ),
    "stir_shaken": StartTemplate(
        agent="carrier",
        instruction=(
            "Lay out the STIR/SHAKEN certificate application via STI-PA. "
            "Confirm CORES FRN + OCN + RMD are all complete first. Walk "
            "through the iconectiv officer-vetting sequence and the "
            "expected 4-8 week timeline. Queue the action for operator "
            "approval."
        ),
    ),
    "section_214": StartTemplate(
        agent="compliance",
        instruction=(
            "Draft the FCC Section 214 application for international "
            "authority. Queue for operator approval."
        ),
    ),
    "entity_formation": StartTemplate(
        agent="intake",
        instruction=(
            "Help the operator confirm the client's legal entity is "
            "properly formed. Check intake fields (legal_name, "
            "entity_type, formation_state). If anything's missing, draft "
            "a follow-up email to the client."
        ),
    ),
    "ein": StartTemplate(
        agent="intake",
        instruction=(
            "Walk the operator through the SS-4 / EIN online application "
            "based on the captured intake."
        ),
    ),
    "bank_account": StartTemplate(
        agent="intake",
        instruction=(
            "Draft a checklist of what the client needs to open a "
            "business bank account (formation cert + EIN letter + officer "
            "ID + proof of address — they should already have these from "
            "intake)."
        ),
    ),
}


def template_for(app_type: str) -> StartTemplate:
    """Resolve per-app instruction. Handles dynamic state_cpcn:XX
    and carrier:NAME forms."""
    if app_type in _TEMPLATES:
        return _TEMPLATES[app_type]
    if app_type.startswith("state_cpcn:"):
        state = app_type.split(":", 1)[1]
        return StartTemplate(
            agent="state_licensing",
            instruction=(
                f"Draft the state CPCN / SPCOA / Section 99 application "
                f"for {state} for this client. Use lookup_state_requirement "
                f"first to confirm current requirements; flag notarization / "
                f"surety bond / hearing requirements explicitly. Queue the "
                f"action for operator approval."
            ),
        )
    if app_type.startswith("carrier:"):
        name = app_type.split(":", 1)[1]
        return StartTemplate(
            agent="carrier",
            instruction=(
                f"Draft the wholesale carrier interconnect application "
                f"for {name} for this client. Use lookup_carrier_specs "
                f"first to confirm the carrier's current onboarding "
                f"bundle. Carrier prerequisites: OCN issued, RMD entry "
                f"live, STIR/SHAKEN cert in hand. Queue for operator "
                f"approval."
            ),
        )
    # Unknown type — fall back to PM
    return StartTemplate(
        agent="pm",
        instruction=(
            f"Coordinate work on the '{app_type}' filing for this client. "
            "Read the intake, identify what's needed, and delegate via "
            "assign_task."
        ),
    )


def _prereqs_for(app_type: str) -> list[str]:
    """Static prereqs lookup. Unknown types get an empty list."""
    return PREREQS.get(app_type, [])


@dataclass
class ReadinessItem:
    application_type: str
    application_id: uuid.UUID | None  # None if the row hasn't been synced yet
    label: str
    owner_agent: str
    instruction: str
    stage: str
    current_agent: str | None = None
    blocked_on: list[str] | None = None  # human-readable for blocked items
    external_ref: str | None = None
    notes: str | None = None


@dataclass
class ReadinessSnapshot:
    intake_complete: bool
    intake_missing_fields: list[str]
    intake_missing_documents: list[str]
    ready: list[ReadinessItem]
    blocked: list[ReadinessItem]
    in_flight: list[ReadinessItem]
    complete: list[ReadinessItem]


async def compute(client_id: uuid.UUID, db: AsyncSession) -> ReadinessSnapshot:
    # 1. Intake completeness.
    intake = await db.scalar(
        select(ClientIntake).where(ClientIntake.client_id == client_id)
    )
    doc_types = set(
        (
            await db.scalars(
                select(Document.type).where(
                    Document.client_id == client_id,
                    Document.s3_key.is_not(None),
                )
            )
        ).all()
    )
    completeness = evaluate_intake(intake, doc_types)

    # 2. Pull all applications, build type→stage map.
    apps = list(
        (
            await db.scalars(
                select(Application).where(Application.client_id == client_id)
            )
        ).all()
    )
    by_type: dict[str, Application] = {a.type: a for a in apps}
    stage_of: dict[str, str] = {a.type: a.stage for a in apps}

    ready: list[ReadinessItem] = []
    blocked: list[ReadinessItem] = []
    in_flight: list[ReadinessItem] = []
    complete: list[ReadinessItem] = []

    for app in apps:
        spec = spec_for(app.type)
        tmpl = template_for(app.type)
        if app.stage in _DONE:
            complete.append(
                ReadinessItem(
                    application_type=app.type,
                    application_id=app.id,
                    label=spec.label if spec else app.type,
                    owner_agent=tmpl.agent,
                    instruction=tmpl.instruction,
                    stage=app.stage,
                    current_agent=app.current_agent,
                    external_ref=app.external_ref,
                )
            )
            continue
        if app.stage in _IN_FLIGHT_STAGES:
            in_flight.append(
                ReadinessItem(
                    application_type=app.type,
                    application_id=app.id,
                    label=spec.label if spec else app.type,
                    owner_agent=tmpl.agent,
                    instruction=tmpl.instruction,
                    stage=app.stage,
                    current_agent=app.current_agent,
                    notes=app.notes,
                )
            )
            continue
        # Not started — check prereqs.
        unmet: list[str] = []
        for prereq in _prereqs_for(app.type):
            prereq_stage = stage_of.get(prereq)
            if prereq_stage not in _DONE:
                prereq_label = label_for(prereq)
                unmet.append(
                    f"{prereq_label} must be complete"
                    + (
                        f" (currently {prereq_stage or 'not synced'})"
                        if prereq_stage != STAGE_NOT_STARTED
                        else ""
                    )
                )
        # Intake gates ALL agent-driven work; you can't draft an FCC
        # filing without legal name + officer info, etc.
        if not completeness.complete:
            unmet.append("Intake must be complete (missing fields or docs)")
        item = ReadinessItem(
            application_type=app.type,
            application_id=app.id,
            label=spec.label if spec else app.type,
            owner_agent=tmpl.agent,
            instruction=tmpl.instruction,
            stage=app.stage,
            blocked_on=unmet if unmet else None,
        )
        if unmet:
            blocked.append(item)
        else:
            ready.append(item)

    return ReadinessSnapshot(
        intake_complete=completeness.complete,
        intake_missing_fields=completeness.missing_fields,
        intake_missing_documents=completeness.missing_documents,
        ready=ready,
        blocked=blocked,
        in_flight=in_flight,
        complete=complete,
    )
