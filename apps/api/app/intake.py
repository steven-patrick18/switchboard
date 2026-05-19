"""Canonical capture-once requirements for a US VoIP launch.

Single source of truth: the completeness check enforces it, agents read
the same intake to auto-fill filings, and the required-DOCUMENT set is
decided automatically per client from the intake (entity, international
intent, target states) rather than a fixed list. Each required document
carries whether it is mandatory (shown with `*`) and whether it must be
a scan (physical / notarized / ID)."""

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


@dataclass(frozen=True)
class RequiredDoc:
    key: str
    label: str
    mandatory: bool  # rendered with `*`
    needs_scan: bool  # physical / notarized / ID — must be scanned
    provided: bool = False


# (label, needs_scan). The decision of WHICH apply to a given client is
# made by resolve_required_documents below — not a flat fixed list.
_CATALOG: dict[str, tuple[str, bool]] = {
    "formation_certificate": ("Certificate of Formation / Articles", False),
    "ein_letter": ("IRS EIN letter (CP-575)", False),
    "officer_government_id": ("Officer government-issued ID", True),
    "proof_of_address": ("Proof of principal business address", True),
    "banking_letter": ("Bank letter for carrier deposits", True),
    "corporate_authorization": ("Board resolution / signing authority", True),
    "section_214_support": ("FCC Section 214 international support", False),
    "state_cpcn_notarized": ("Notarized state CPCN packet", True),
}

_BASE = (
    "formation_certificate",
    "ein_letter",
    "officer_government_id",
    "proof_of_address",
    "banking_letter",
    "corporate_authorization",
)


def catalog_entry(key: str) -> tuple[str, bool] | None:
    """(label, needs_scan) for a known document key, else None."""
    return _CATALOG.get(key)


def catalog_keys() -> list[str]:
    return list(_CATALOG)


def _doc(key: str) -> RequiredDoc:
    label, needs_scan = _CATALOG[key]
    return RequiredDoc(key=key, label=label, mandatory=True, needs_scan=needs_scan)


def resolve_required_documents(intake: ClientIntake | None) -> list[RequiredDoc]:
    """Automatically decide the mandated documents for THIS client from
    its intake. Deterministic policy (the codified operator judgment);
    works without an LLM/key. International intent and target states pull
    in extra mandated docs."""
    keys = list(_BASE)
    if intake is not None:
        if getattr(intake, "intends_international", False):
            keys.append("section_214_support")
        if intake.target_states:
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
    missing_documents = [
        d.key for d in required if d.mandatory and not d.provided
    ]
    return Completeness(
        missing_fields=missing_fields,
        missing_documents=missing_documents,
        required_documents=required,
    )
