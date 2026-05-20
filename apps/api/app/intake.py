"""Canonical capture-once requirements for a US VoIP launch.

Single source of truth: the completeness check enforces it, agents read
the same intake to auto-fill filings, and the required-DOCUMENT set is
decided automatically per client from the intake (entity, international
intent, target states) rather than a fixed list.

Each required document carries:
- `mandatory` (rendered with `*`): if False, missing the doc does NOT
  block intake submission. Used for documents that are *acquired during
  the launch* (bank letters, signing authority, notarized state CPCN
  packets) — the client doesn't have them at intake time, and asking
  for them up front confuses real-world onboarding.
- `needs_scan`: a physical / notarized / ID artifact that has to be
  scanned (vs. a born-digital download like an EIN letter).
- `phase`: 'intake' (need it now to begin) or 'launch' (operator will
  draft / request it during the launch). The UI uses this to show a
  "needed at launch" badge instead of the red asterisk.
"""

from dataclasses import dataclass, replace

from app.models.client_intake import ClientIntake

# Intake fields that must be captured before a client is considered fully
# onboarded.
REQUIRED_INTAKE_FIELDS: tuple[str, ...] = (
    "legal_name",
    "entity_type",
    "formation_state",
    "ein",
    "principal_address",
    "officer_name",
    "officer_title",
    "officer_email",
    "primary_contact_name",
    "primary_contact_email",
    "primary_contact_phone",
    "target_states",
    "estimated_monthly_revenue",
)

PHASE_INTAKE = "intake"   # blocker — must have it to submit intake
PHASE_LAUNCH = "launch"   # deferred — needed during the launch, not now


@dataclass(frozen=True)
class RequiredDoc:
    key: str
    label: str
    mandatory: bool      # rendered with `*` red asterisk; blocks completeness
    needs_scan: bool     # physical / notarized / ID — must be scanned
    phase: str           # PHASE_INTAKE | PHASE_LAUNCH
    provided: bool = False


# (label, needs_scan, phase). The decision of WHICH apply to a given
# client is made by resolve_required_documents below — not a flat list.
_CATALOG: dict[str, tuple[str, bool, str]] = {
    # Founder stage — personal KYC + basis to form the LLC. All blockers.
    "ssn_card": ("Social Security card or SSN/ITIN confirmation", True, PHASE_INTAKE),
    "drivers_license": ("Driver's license or government photo ID", True, PHASE_INTAKE),
    "founder_photo": ("Recent photo of the founder", False, PHASE_INTAKE),
    "utility_bill": ("Utility bill — proof of personal address", True, PHASE_INTAKE),

    # Entity stage — what the client genuinely has at intake time.
    "formation_certificate": ("Certificate of Formation / Articles", False, PHASE_INTAKE),
    "ein_letter": ("IRS EIN letter (CP-575)", False, PHASE_INTAKE),
    "officer_government_id": ("Officer government-issued ID", True, PHASE_INTAKE),
    "proof_of_address": ("Proof of principal business address", True, PHASE_INTAKE),

    # Entity stage — DEFERRED: acquired during the launch, not at intake.
    # The bank issues the letter once you open the carrier-funding account;
    # signing authority is drafted when the first MSA needs signature; the
    # notarized CPCN packet is drafted BY the operator for the client to
    # sign during state filings. Asking the client for them at intake is
    # wrong.
    "banking_letter": ("Bank letter for carrier deposits", True, PHASE_LAUNCH),
    "corporate_authorization": ("Member/board signing authority", True, PHASE_LAUNCH),
    "section_214_support": ("FCC Section 214 international support", False, PHASE_LAUNCH),
    "state_cpcn_notarized": ("Notarized state CPCN packet", True, PHASE_LAUNCH),
}

# A single founder starts here — personal docs, pre-formation.
_FOUNDER = (
    "ssn_card",
    "drivers_license",
    "founder_photo",
    "utility_bill",
)

# Once the entity is formed (EIN captured) the corporate docs apply.
# Order matters for UI display — intake-phase docs first, launch-phase
# docs after, so the operator/client see "what we need now" up top.
_ENTITY_BASE = (
    "formation_certificate",
    "ein_letter",
    "officer_government_id",
    "proof_of_address",
    "banking_letter",          # launch
    "corporate_authorization", # launch
)

STAGE_FOUNDER = "founder"
STAGE_ENTITY = "entity"


def intake_stage(intake: ClientIntake | None) -> str:
    """Founder stage until the company is formed (EIN captured), then
    entity stage. This is what lets a single person start from scratch."""
    ein = getattr(intake, "ein", None) if intake is not None else None
    return STAGE_ENTITY if (ein or "").strip() else STAGE_FOUNDER


def catalog_entry(key: str) -> tuple[str, bool] | None:
    """(label, needs_scan) for a known document key, else None.
    Kept for backward compatibility; new callers should use the full
    catalog directly via _CATALOG[key]."""
    entry = _CATALOG.get(key)
    return (entry[0], entry[1]) if entry else None


def catalog_keys() -> list[str]:
    return list(_CATALOG)


def _doc(key: str) -> RequiredDoc:
    label, needs_scan, phase = _CATALOG[key]
    return RequiredDoc(
        key=key,
        label=label,
        # Only PHASE_INTAKE docs block completeness. PHASE_LAUNCH docs
        # appear in the list (so the operator sees what's coming) but
        # don't block intake submission.
        mandatory=(phase == PHASE_INTAKE),
        needs_scan=needs_scan,
        phase=phase,
    )


def resolve_required_documents(intake: ClientIntake | None) -> list[RequiredDoc]:
    """Automatically decide the mandated documents for THIS client and
    its onboarding stage. A single founder with no entity yet is asked
    only for personal docs they can actually give now; the corporate
    documents become mandated once the entity is formed (EIN captured).
    International intent and target states pull in extra entity-stage
    docs. Deterministic — works without an LLM/key."""
    if intake_stage(intake) == STAGE_FOUNDER:
        return [_doc(k) for k in _FOUNDER]
    keys = list(_ENTITY_BASE)
    if getattr(intake, "intends_international", False):
        keys.append("section_214_support")
    if intake is not None and intake.target_states:
        keys.append("state_cpcn_notarized")
    return [_doc(k) for k in keys]


def mandatory_document_keys(intake: ClientIntake | None = None) -> list[str]:
    return [d.key for d in resolve_required_documents(intake) if d.mandatory]


def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, list | dict):
        return len(value) == 0
    return False


@dataclass
class Completeness:
    missing_fields: list[str]
    missing_documents: list[str]
    required_documents: list[RequiredDoc]
    stage: str

    @property
    def complete(self) -> bool:
        return not self.missing_fields and not self.missing_documents


def evaluate(
    intake: ClientIntake | None, provided_document_types: set[str]
) -> Completeness:
    missing_fields = (
        list(REQUIRED_INTAKE_FIELDS)
        if intake is None
        else [
            f
            for f in REQUIRED_INTAKE_FIELDS
            if _is_empty(getattr(intake, f, None))
        ]
    )
    required = [
        replace(d, provided=d.key in provided_document_types)
        for d in resolve_required_documents(intake)
    ]
    # Only mandatory (PHASE_INTAKE) docs block submission. Deferred docs
    # show in the UI but stay green-light for intake completion.
    missing_documents = [
        d.key for d in required if d.mandatory and not d.provided
    ]
    return Completeness(
        missing_fields=missing_fields,
        missing_documents=missing_documents,
        required_documents=required,
        stage=intake_stage(intake),
    )
