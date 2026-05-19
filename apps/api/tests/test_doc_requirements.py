"""The mandated document set is decided per client from its intake, and
each item is flagged mandatory (UI `*`) and whether it needs a scan.
Pure unit — no DB, no API spend.
"""

from types import SimpleNamespace

from app.intake import evaluate, mandatory_document_keys, resolve_required_documents

_BASE = {
    "formation_certificate",
    "ein_letter",
    "officer_government_id",
    "proof_of_address",
    "banking_letter",
    "corporate_authorization",
}


def test_base_set_when_no_intake():
    docs = resolve_required_documents(None)
    assert {d.key for d in docs} == _BASE
    assert all(d.mandatory for d in docs)
    by = {d.key: d for d in docs}
    assert by["officer_government_id"].needs_scan is True
    assert by["proof_of_address"].needs_scan is True
    assert by["ein_letter"].needs_scan is False
    assert mandatory_document_keys(None) == [d.key for d in docs]


def test_international_pulls_section_214():
    intake = SimpleNamespace(intends_international=True, target_states=[])
    keys = {d.key for d in resolve_required_documents(intake)}
    assert "section_214_support" in keys
    assert "state_cpcn_notarized" not in keys


def test_target_states_pull_notarized_scan_doc():
    intake = SimpleNamespace(intends_international=False, target_states=["TX"])
    by = {d.key: d for d in resolve_required_documents(intake)}
    assert "state_cpcn_notarized" in by
    assert by["state_cpcn_notarized"].needs_scan is True
    assert by["state_cpcn_notarized"].mandatory is True


def test_evaluate_marks_provided_and_missing():
    c = evaluate(None, {"ein_letter"})
    by = {d.key: d for d in c.required_documents}
    assert by["ein_letter"].provided is True
    assert by["formation_certificate"].provided is False
    assert "ein_letter" not in c.missing_documents
    assert "formation_certificate" in c.missing_documents
    # Fields still missing (no intake) → not complete.
    assert c.complete is False
