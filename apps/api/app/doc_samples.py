"""Client-facing spec/template for each mandated document, so the
operator can send the client exactly what to provide. Assembled from the
shared catalog (label + scan requirement) plus a per-document body."""

from app.intake import catalog_entry, catalog_keys

# What the client must send for each document type.
_BODY: dict[str, str] = {
    "formation_certificate": (
        "The state-issued document proving the entity was formed "
        "(Certificate of Formation / Articles of Organization or "
        "Incorporation).\n"
        "Must clearly show: exact legal entity name, state of formation, "
        "formation date, and the state filing stamp/seal.\n"
        "Send the state-stamped copy, not a draft."
    ),
    "ein_letter": (
        "IRS EIN confirmation: the CP-575 assignment letter, or a 147C "
        "letter if the CP-575 is lost.\n"
        "Must show the exact legal entity name and the 9-digit EIN."
    ),
    "officer_government_id": (
        "Government-issued photo ID of the officer who will sign filings "
        "(passport or driver's license).\n"
        "Must be current (not expired), in color, with all four corners "
        "and the photo clearly legible."
    ),
    "proof_of_address": (
        "Proof of the principal business address, dated within the last "
        "90 days: a utility bill, signed lease, or bank statement.\n"
        "Must show the entity name and the business address."
    ),
    "banking_letter": (
        "A bank letter on bank letterhead (or a bank-verified voided "
        "check) confirming the business operating account that carrier "
        "deposits will be paid from.\n"
        "Must show entity name, account/routing confirmation, and be "
        "signed/stamped by the bank."
    ),
    "corporate_authorization": (
        "A board resolution / written consent authorizing the named "
        "officer to sign carrier agreements and regulatory filings.\n\n"
        "TEMPLATE — fill the brackets, then sign:\n"
        "------------------------------------------------------------\n"
        "RESOLUTION OF [ENTITY LEGAL NAME]\n"
        "The undersigned, constituting the [board/members] of [ENTITY], "
        "resolve that [OFFICER NAME], [TITLE], is authorized to execute "
        "and submit on the entity's behalf all carrier onboarding "
        "agreements, FCC and state regulatory filings, and related "
        "documents.\n\n"
        "Signature: ____________________   Date: __________\n"
        "Name/Title: [OFFICER NAME], [TITLE]\n"
        "------------------------------------------------------------"
    ),
    "section_214_support": (
        "Information supporting an FCC Section 214 international "
        "authorization: the countries/regions to be served and a "
        "disclosure of any 10%+ foreign ownership (name, citizenship, "
        "percentage)."
    ),
    "state_cpcn_notarized": (
        "The state CPCN application packet for each target state, "
        "wet-ink signed by the officer and NOTARIZED.\n"
        "Send a scan of the fully notarized original (notary stamp and "
        "signature must be visible)."
    ),
}


def get_sample(key: str) -> tuple[str, str] | None:
    """(filename, text) for a document key, or None if unknown."""
    entry = catalog_entry(key)
    if entry is None or key not in _BODY:
        return None
    label, needs_scan = entry
    scan = (
        "FORMAT: a clear, complete SCAN of the physical/notarized "
        "document is REQUIRED (PDF or image, legible, uncropped).\n"
        if needs_scan
        else "FORMAT: a clear PDF or scan is acceptable.\n"
    )
    text = (
        f"SWITCHBOARD — DOCUMENT REQUEST\n"
        f"Document: {label}\n"
        f"{'=' * 60}\n\n"
        f"{_BODY[key]}\n\n"
        f"{scan}\n"
        f"Please reply to this email with the document attached. If "
        f"anything is unclear, ask before sending — incorrect documents "
        f"delay the launch.\n"
    )
    return f"{key}-requirements.txt", text


def all_sample_keys() -> list[str]:
    return [k for k in catalog_keys() if k in _BODY]


def _slug(name: str) -> str:
    out = "".join(ch.lower() if ch.isalnum() else "-" for ch in name)
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-") or "client"


def build_request_pack(client_name: str, docs) -> tuple[str, str]:
    """One emailable packet for a client: cover note, a checklist (with
    * for mandatory and (SCAN) where a scan is required), then each
    document's spec. `docs` is the client's resolved required documents
    so the pack is tailored (international / target states included)."""
    lines: list[str] = [
        "SWITCHBOARD — DOCUMENT REQUEST PACK",
        f"Client: {client_name}",
        "=" * 60,
        "",
        "Please provide every document below. Items marked * are "
        "mandatory. Items marked (SCAN) must be a clear scanned copy of "
        "the physical / notarized original. Incorrect or missing "
        "documents delay the launch — ask before sending if unsure.",
        "",
        "CHECKLIST",
    ]
    for d in docs:
        star = " *" if d.mandatory else ""
        scan = " (SCAN)" if d.needs_scan else ""
        lines.append(f"  [ ] {d.label}{star}{scan}")
    lines.append("")
    lines.append("=" * 60)
    for d in docs:
        flags = (" *" if d.mandatory else "") + (
            "  (SCAN REQUIRED)" if d.needs_scan else ""
        )
        lines += [
            "",
            f"## {d.label}{flags}",
            _BODY.get(d.key, "(specification pending)"),
            "",
            "-" * 60,
        ]
    return f"{_slug(client_name)}-document-request.txt", "\n".join(lines) + "\n"
