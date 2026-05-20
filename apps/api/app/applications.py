"""Single source of truth: which applications are mandated for THIS
client based on its intake.

Mirrors the pattern in app/intake.py::resolve_required_documents — the
operator never has to remember the list manually; it's derived from
intake state. New target_states / international intent flip the right
applications on automatically.

Application TYPES are short machine codes; the operator GUI looks up a
human label via label_for(type) and a default owning-agent via
default_agent_for(type).
"""

from dataclasses import dataclass

from app.models.client_intake import ClientIntake

# Type constants — used in code AND in DB rows. Stable strings.
TYPE_ENTITY_FORMATION = "entity_formation"
TYPE_EIN = "ein"
TYPE_BANK_ACCOUNT = "bank_account"
TYPE_CORES_FRN = "cores_frn"
TYPE_OCN = "ocn"
TYPE_FCC_499 = "fcc_499"
TYPE_RMD = "rmd"
TYPE_STIR_SHAKEN = "stir_shaken"
TYPE_SECTION_214 = "section_214"

# The five FCC/NECA filings that MUST be in place before a carrier
# will provision live voice traffic. Order matters — earlier ones are
# prerequisites for later ones.
MANDATORY_FIVE = (
    TYPE_CORES_FRN,      # 1. Get an FRN — required to file 499 or anything else with the FCC
    TYPE_OCN,            # 2. NECA OCN — required to enter RMD and signal with most carriers
    TYPE_FCC_499,        # 3. FCC 499 filer registration — required to legally provide service
    TYPE_RMD,            # 4. Robocall Mitigation Database — required before carriers will accept traffic
    TYPE_STIR_SHAKEN,    # 5. STIR/SHAKEN cert — required to sign outbound calls
)
# state_cpcn:<XX> — one per target state
# carrier:<name>  — one per intended wholesale carrier (operator picks)


@dataclass(frozen=True)
class ApplicationSpec:
    type: str
    label: str
    description: str
    default_agent: str  # which agent typically owns it


# Static catalog. State CPCN + carrier rows are generated dynamically.
_CATALOG: dict[str, ApplicationSpec] = {
    TYPE_ENTITY_FORMATION: ApplicationSpec(
        type=TYPE_ENTITY_FORMATION,
        label="Entity formation",
        description="LLC / Corp registration in the formation state.",
        default_agent="intake",
    ),
    TYPE_EIN: ApplicationSpec(
        type=TYPE_EIN,
        label="IRS EIN",
        description="Employer Identification Number from the IRS (Form SS-4).",
        default_agent="intake",
    ),
    TYPE_BANK_ACCOUNT: ApplicationSpec(
        type=TYPE_BANK_ACCOUNT,
        label="Business bank account",
        description="Operating account — needed before carrier deposits.",
        default_agent="intake",
    ),
    TYPE_CORES_FRN: ApplicationSpec(
        type=TYPE_CORES_FRN,
        label="FCC CORES (FRN)",
        description=(
            "FCC Commission Registration System (CORES) registration to "
            "obtain an FRN (FCC Registration Number). PREREQUISITE for "
            "Form 499 and any other FCC filing. Free, ~15 minutes online "
            "at apps.fcc.gov/cores. Required before OCN application too "
            "(NECA asks for the FRN)."
        ),
        default_agent="compliance",
    ),
    TYPE_OCN: ApplicationSpec(
        type=TYPE_OCN,
        label="OCN (NECA)",
        description=(
            "Operating Company Number — issued by NECA via Form "
            "NECA-OCN-2. Operator applies on the client's behalf with "
            "a Letter of Agency. PREREQUISITE for RMD entry and most "
            "wholesale carriers. Timeline 2-3 weeks."
        ),
        default_agent="carrier",
    ),
    TYPE_FCC_499: ApplicationSpec(
        type=TYPE_FCC_499,
        label="FCC Form 499-A",
        description=(
            "Filer registration for USF/TRS contributions. Filed via "
            "USAC E-File system."
        ),
        default_agent="compliance",
    ),
    TYPE_RMD: ApplicationSpec(
        type=TYPE_RMD,
        label="Robocall Mitigation Database",
        description=(
            "RMD entry with mitigation plan and STIR/SHAKEN status. "
            "Requires OCN first."
        ),
        default_agent="compliance",
    ),
    TYPE_STIR_SHAKEN: ApplicationSpec(
        type=TYPE_STIR_SHAKEN,
        label="STIR/SHAKEN token",
        description=(
            "Caller-ID authentication certificate from STI-PA / "
            "iconectiv. Requires OCN + RMD + officer vetting. "
            "Timeline 4-8 weeks."
        ),
        default_agent="carrier",
    ),
    TYPE_SECTION_214: ApplicationSpec(
        type=TYPE_SECTION_214,
        label="FCC Section 214",
        description=(
            "International authority — application to the FCC "
            "International Bureau. Conditional on intends_international."
        ),
        default_agent="compliance",
    ),
}


def _state_cpcn_spec(state: str) -> ApplicationSpec:
    state = state.upper()
    return ApplicationSpec(
        type=f"state_cpcn:{state}",
        label=f"State CPCN — {state}",
        description=(
            f"State-level Certificate of Public Convenience and "
            f"Necessity (or equivalent: SPCOA / Section 99) for "
            f"{state}. Notarization / surety bond may apply."
        ),
        default_agent="state_licensing",
    )


def spec_for(app_type: str) -> ApplicationSpec | None:
    """Resolve a type string to its display spec. Handles the static
    catalog plus dynamic state_cpcn:XX and carrier:NAME forms."""
    if app_type in _CATALOG:
        return _CATALOG[app_type]
    if app_type.startswith("state_cpcn:"):
        return _state_cpcn_spec(app_type.split(":", 1)[1])
    if app_type.startswith("carrier:"):
        name = app_type.split(":", 1)[1]
        return ApplicationSpec(
            type=app_type,
            label=f"Carrier — {name}",
            description=(
                f"Wholesale carrier interconnection with {name}. "
                "Requires OCN + signed MSA + (sometimes) deposit."
            ),
            default_agent="carrier",
        )
    return None


def label_for(app_type: str) -> str:
    spec = spec_for(app_type)
    return spec.label if spec else app_type


def default_agent_for(app_type: str) -> str:
    spec = spec_for(app_type)
    return spec.default_agent if spec else "pm"


def resolve_required_applications(intake: ClientIntake | None) -> list[str]:
    """Derive the launch's required applications from intake state.

    The non-conditional base set is always required for any US VoIP
    launch. State CPCNs are derived from target_states, Section 214
    from intends_international. Carriers stay operator-picked — we
    don't auto-add specific carrier interconnects because the choice
    depends on the client's business model.
    """
    apps: list[str] = []
    has_ein = bool((intake.ein if intake else None) or "")
    if not has_ein:
        # Pre-formation: only the founder-stage applications make sense.
        apps.extend([TYPE_ENTITY_FORMATION, TYPE_EIN, TYPE_BANK_ACCOUNT])
        return apps

    # Entity stage: founder set + the FIVE mandatory filings every US
    # voice carrier must complete before live traffic. Order in this
    # list is also the recommended filing order: CORES (get FRN) →
    # OCN → 499 → RMD → STIR/SHAKEN.
    apps.extend(
        [
            TYPE_ENTITY_FORMATION,
            TYPE_EIN,
            TYPE_BANK_ACCOUNT,
            TYPE_CORES_FRN,   # 1
            TYPE_OCN,         # 2
            TYPE_FCC_499,     # 3
            TYPE_RMD,         # 4
            TYPE_STIR_SHAKEN, # 5
        ]
    )
    if intake is not None and getattr(intake, "intends_international", False):
        apps.append(TYPE_SECTION_214)
    target_states = (intake.target_states if intake else None) or []
    for st in target_states:
        if st and isinstance(st, str):
            apps.append(f"state_cpcn:{st.upper()}")
    return apps
