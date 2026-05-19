"""Per-service catalog of known portal actions an agent can request.

Single source of truth for: which services the agent may target, what
each action does, and which params it needs. Approval cards render
nicer labels from this; the executor validates required params against
it. Free-form actions outside the catalog still work (gracefully), but
the registry is the supported menu.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionSpec:
    service: str
    action: str
    label: str
    description: str
    required_params: tuple[str, ...] = ()
    optional_params: tuple[str, ...] = ()
    tier: str = "T2"  # advisory; the request_portal_action tool is T2


_ENTRIES: tuple[ActionSpec, ...] = (
    ActionSpec(
        service="fcc_cores",
        action="check_filer_status",
        label="FCC CORES — check filer status",
        description="Look up the client's FCC Form 499 filer ID and current status.",
        required_params=("filer_id",),
    ),
    ActionSpec(
        service="fcc_cores",
        action="submit_499_q",
        label="FCC CORES — submit Form 499-Q",
        description="Submit the quarterly Form 499-Q on the client's behalf.",
        required_params=("quarter", "revenue"),
        optional_params=("attachments",),
        tier="T3",
    ),
    ActionSpec(
        service="fcc_cores",
        action="update_rmd_entry",
        label="FCC CORES — update RMD entry",
        description="Update the Robocall Mitigation Database entry.",
        required_params=("mitigation_plan_url",),
    ),
    ActionSpec(
        service="irs",
        action="check_ein_status",
        label="IRS — check EIN status",
        description="Check the EIN assignment/status for the client.",
        required_params=("ein",),
    ),
    ActionSpec(
        service="state_puc_tx",
        action="check_cpcn_status",
        label="Texas PUC — check CPCN status",
        description="Look up the client's Texas CPCN application/status.",
        required_params=("docket_id",),
    ),
    ActionSpec(
        service="carrier_telnyx",
        action="check_account_status",
        label="Telnyx — check wholesale account status",
        description="Verify the wholesale carrier account exists and is in good standing.",
    ),
    ActionSpec(
        service="carrier_telnyx",
        action="submit_credit_app",
        label="Telnyx — submit credit application",
        description="Submit the wholesale credit application package.",
        required_params=("deposit_amount", "estimated_mou"),
        tier="T3",
    ),
    ActionSpec(
        service="bank",
        action="check_balance",
        label="Bank — check operating balance",
        description="Read-only check of the operating account balance (for carrier deposits).",
    ),
    # --- Phase 2 catalog expansion ---
    ActionSpec(
        service="fcc_cores",
        action="register_filer_id",
        label="FCC CORES — register filer ID",
        description="Initial FCC filer ID registration for the entity.",
        required_params=("legal_name", "ein", "address"),
    ),
    ActionSpec(
        service="fcc_cores",
        action="submit_section_214",
        label="FCC CORES — submit Section 214 international authority",
        description="Apply for FCC Section 214 authorization to provide international service.",
        required_params=("foreign_ownership", "countries"),
        tier="T3",
    ),
    ActionSpec(
        service="iconectiv",
        action="check_osp_status",
        label="iconectiv STI-PA — check OSP/OCN status",
        description="Look up the client's OSP/OCN and STIR/SHAKEN attestation status.",
        required_params=("osp_code",),
    ),
    ActionSpec(
        service="iconectiv",
        action="request_token_issuance",
        label="iconectiv STI-PA — request token issuance",
        description="Request a STIR/SHAKEN signing token (requires live officer vetting).",
        required_params=("officer_name", "osp_code"),
        tier="T3",
    ),
    ActionSpec(
        service="irs",
        action="submit_ein_application",
        label="IRS — submit EIN application (SS-4)",
        description="File Form SS-4 to obtain an EIN for the entity.",
        required_params=("entity_type", "responsible_party_ssn"),
        tier="T3",
    ),
    ActionSpec(
        service="state_puc_ca",
        action="file_cpcn",
        label="California CPUC — file CPCN application",
        description="Submit the California CPCN application packet.",
        required_params=("service_areas",),
        tier="T3",
    ),
    ActionSpec(
        service="carrier_bandwidth",
        action="submit_credit_app",
        label="Bandwidth — submit credit application",
        description="Submit the Bandwidth wholesale credit application package.",
        required_params=("deposit_amount", "estimated_mou"),
        tier="T3",
    ),
    ActionSpec(
        service="carrier_inteliquent",
        action="check_account_status",
        label="Inteliquent — check wholesale account status",
        description="Verify the Inteliquent wholesale account exists and is in good standing.",
    ),
)

ACTIONS: dict[tuple[str, str], ActionSpec] = {
    (e.service, e.action): e for e in _ENTRIES
}


def get_action(service: str, action: str) -> ActionSpec | None:
    return ACTIONS.get((service, action))


def list_actions() -> list[ActionSpec]:
    return list(_ENTRIES)


def services() -> list[str]:
    seen: list[str] = []
    for e in _ENTRIES:
        if e.service not in seen:
            seen.append(e.service)
    return seen


def missing_params(spec: ActionSpec, params: dict | None) -> list[str]:
    p = params or {}
    return [k for k in spec.required_params if not p.get(k)]
