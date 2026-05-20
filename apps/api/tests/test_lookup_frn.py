"""fcc_cores:lookup_frn lets the carrier agent retrieve an existing FRN
from CORES without asking the operator, so NECA-OCN-2 can be drafted
with the real FRN instead of [FRN: TBD].

Covers: catalog entry, demo handler shape, missing-params behavior, and
that the carrier agent's prompt explicitly instructs lookup_frn-first
before falling back to TBD.
"""

import pytest

from app.agents.carrier import CARRIER_AGENT
from app.integrations import _DEMO_HANDLERS
from app.portal_actions import ACTIONS, get_action, missing_params


def test_lookup_frn_is_in_the_catalog():
    spec = get_action("fcc_cores", "lookup_frn")
    assert spec is not None
    assert spec.tier == "T2"  # read-only; safe to auto-execute in autonomous mode
    assert "legal_name" in spec.required_params
    assert "ein" in spec.required_params
    assert ("fcc_cores", "lookup_frn") in ACTIONS


def test_lookup_frn_missing_params_check():
    spec = get_action("fcc_cores", "lookup_frn")
    assert missing_params(spec, {}) == ["legal_name", "ein"]
    assert missing_params(spec, {"legal_name": "Acme LLC"}) == ["ein"]
    assert missing_params(spec, {"legal_name": "Acme LLC", "ein": "12-3456789"}) == []


@pytest.mark.asyncio
async def test_demo_lookup_frn_returns_deterministic_frn():
    handler = _DEMO_HANDLERS[("fcc_cores", "lookup_frn")]
    out = await handler(
        "fake-secret",
        {"legal_name": "Amano Telecom LLC", "ein": "39-2196239"},
    )
    assert out.backend == "demo"
    assert out.status == "FOUND"
    # FRN derived from EIN digits; 10 digits exact.
    assert out.detail["frn"] == "0392196239"
    assert out.detail["legal_name"] == "Amano Telecom LLC"
    # Deterministic: re-running yields the same FRN.
    out2 = await handler(
        "fake-secret",
        {"legal_name": "Amano Telecom LLC", "ein": "39-2196239"},
    )
    assert out2.detail["frn"] == out.detail["frn"]


@pytest.mark.asyncio
async def test_demo_lookup_frn_handles_missing_params_gracefully():
    handler = _DEMO_HANDLERS[("fcc_cores", "lookup_frn")]
    out = await handler("fake", {})
    assert out.status == "MISSING_PARAMS"
    assert "legal_name" in out.detail["missing"]
    assert "ein" in out.detail["missing"]


def test_carrier_prompt_directs_lookup_frn_first():
    """Regression: v1.3.11 hardened the carrier prompt to call
    lookup_frn proactively rather than asking the operator for the
    FRN. If a future edit removes that direction, the agent will
    regress to TBD-placeholder behavior."""
    p = CARRIER_AGENT.system_prompt
    assert "lookup_frn" in p
    assert "fcc_cores" in p
    # And it should still have the bias-toward-action / never-suggest-
    # a-different-agent invariants from earlier rounds.
    assert "BIAS TOWARD ACTION" in p
    assert "NEVER suggest a different agent" in p
