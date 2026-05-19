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
