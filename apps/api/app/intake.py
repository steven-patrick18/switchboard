"""Canonical capture-once requirements for a US VoIP launch.

This is the single source of truth: the completeness check enforces it,
and agents read the same intake record to auto-fill filings without
re-asking the client (the brief's form-field memory tactic)."""

from dataclasses import dataclass

from app.models.client_intake import ClientIntake

# Intake fields that must be captured before a client is considered fully
# onboarded. Kept to the mandatory core shared across the whole process;
# per-state CPCN extras are playbook-driven and live in `extra`.
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

# Documents the client must provide at onboarding for the whole launch.
REQUIRED_DOCUMENT_TYPES: tuple[str, ...] = (
    "formation_certificate",
    "ein_letter",
    "officer_government_id",
    "proof_of_address",
    "banking_letter",
    "corporate_authorization",
)


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

    @property
    def complete(self) -> bool:
        return not self.missing_fields and not self.missing_documents


def evaluate(
    intake: ClientIntake | None, provided_document_types: set[str]
) -> Completeness:
    if intake is None:
        return Completeness(
            missing_fields=list(REQUIRED_INTAKE_FIELDS),
            missing_documents=list(REQUIRED_DOCUMENT_TYPES),
        )
    missing_fields = [
        f for f in REQUIRED_INTAKE_FIELDS if _is_empty(getattr(intake, f, None))
    ]
    missing_documents = [
        d for d in REQUIRED_DOCUMENT_TYPES if d not in provided_document_types
    ]
    return Completeness(missing_fields=missing_fields, missing_documents=missing_documents)
