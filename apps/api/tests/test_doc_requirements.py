"""The mandated document set is decided per client AND per onboarding
stage. A single founder with no entity yet is asked only for the
personal docs they can give now; corporate docs become mandated once
the entity is formed (EIN captured). Pure unit — no DB, no API spend.
"""

from types import SimpleNamespace

from app.intake import (
    evaluate,
    intake_stage,
    mandatory_document_keys,
    resolve_required_documents,
)

_FOUNDER = {"ssn_card", "drivers_license", "founder_photo", "utility_bill"}
_ENTITY = {
    "formation_certificate",
    "ein_letter",
    "officer_government_id",
    "proof_of_address",
    "banking_letter",
    "corporate_authorization",
}


def test_single_founder_starts_with_personal_docs():
    # No intake / no EIN → a single person can start here.
    assert intake_stage(None) == "founder"
    docs = resolve_required_documents(None)
    assert {d.key for d in docs} == _FOUNDER
    assert all(d.mandatory for d in docs)
    by = {d.key: d for d in docs}
    assert by["ssn_card"].needs_scan is True
    assert by["drivers_license"].needs_scan is True
    assert by["founder_photo"].needs_scan is False  # a photo, not a scan
    assert mandatory_document_keys(None) == [d.key for d in docs]

    # An intake started but not yet formed is still founder stage.
    started = SimpleNamespace(ein=None, intends_international=False, target_states=[])
    assert intake_stage(started) == "founder"
    assert {d.key for d in resolve_required_documents(started)} == _FOUNDER


def test_entity_docs_apply_once_formed():
    formed = SimpleNamespace(
        ein="99-1234567", intends_international=False, target_states=[]
    )
    assert intake_stage(formed) == "entity"
    assert {d.key for d in resolve_required_documents(formed)} == _ENTITY


def test_entity_conditionals_international_and_states():
    intl = SimpleNamespace(
        ein="99-1234567", intends_international=True, target_states=[]
    )
    assert "section_214_support" in {
        d.key for d in resolve_required_documents(intl)
    }

    states = SimpleNamespace(
        ein="99-1234567", intends_international=False, target_states=["TX"]
    )
    by = {d.key: d for d in resolve_required_documents(states)}
    assert "state_cpcn_notarized" in by
    assert by["state_cpcn_notarized"].needs_scan is True


def test_evaluate_marks_provided_and_missing_founder_stage():
    c = evaluate(None, {"drivers_license"})
    assert c.stage == "founder"
    by = {d.key: d for d in c.required_documents}
    assert by["drivers_license"].provided is True
    assert by["ssn_card"].provided is False
    assert "drivers_license" not in c.missing_documents
    assert "ssn_card" in c.missing_documents
    assert c.complete is False
