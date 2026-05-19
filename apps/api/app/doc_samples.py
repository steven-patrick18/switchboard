"""Client-facing spec/template for each mandated document, so the
operator can send the client exactly what to provide. Assembled from the
shared catalog (label + scan requirement) plus a per-document body."""

from app.intake import catalog_entry, catalog_keys

# What the client must send for each document type.
_BODY: dict[str, str] = {
    "ssn_card": (
        "A clear scan of the founder's Social Security card, or an "
        "SSA/IRS document showing the SSN (or an ITIN confirmation). Used "
        "for KYC and to form the entity / obtain the EIN. Do not send "
        "this over unencrypted channels — use the secure upload."
    ),
    "drivers_license": (
        "The founder's unexpired driver's license or government photo ID "
        "— color scan of the front (and back if the address is on it), "
        "all four corners visible and legible."
    ),
    "founder_photo": (
        "A recent, clear passport-style photo of the founder (head and "
        "shoulders, plain background, good lighting). Used for carrier "
        "KYC. A phone photo is fine — no scan needed."
    ),
    "utility_bill": (
        "A utility bill (electric, water, gas, or internet) in the "
        "founder's name, dated within the last 90 days, showing the "
        "name and residential address."
    ),
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


def _xml(text: str) -> str:
    """Escape for ReportLab Paragraph markup; preserve line breaks."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def build_request_pack_pdf(client_name: str, docs) -> tuple[str, bytes]:
    """One emailable, client-ready PDF: cover note, a checklist (with *
    for mandatory and (SCAN) where a scan is required), then each
    document's spec. `docs` is the client's resolved required documents
    so the pack is tailored (international / target states included)."""
    from io import BytesIO

    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
    )

    styles = getSampleStyleSheet()
    mono = ParagraphStyle(
        "Mono",
        parent=styles["Code"],
        fontSize=8.5,
        leading=11,
        alignment=TA_LEFT,
    )
    body = ParagraphStyle(
        "Body", parent=styles["Normal"], fontSize=10.5, leading=15
    )
    docname = ParagraphStyle(
        "DocName",
        parent=styles["Heading2"],
        spaceBefore=16,
        textColor="#1e293b",
    )

    buf = BytesIO()
    pdf = SimpleDocTemplate(
        buf,
        pagesize=letter,
        title=f"Document Request — {client_name}",
        leftMargin=0.9 * inch,
        rightMargin=0.9 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.8 * inch,
    )
    story = [
        Paragraph(f"Document Request — {_xml(client_name)}", styles["Title"]),
        Paragraph(
            "Switchboard · The AI Operator Platform", styles["Italic"]
        ),
        Spacer(1, 16),
        Paragraph(
            "Please provide every document below. Items marked "
            "<b>*</b> are mandatory. Items marked <b>(SCAN)</b> must be a "
            "clear scanned copy of the physical or notarized original. "
            "Incorrect or missing documents delay the launch — ask before "
            "sending if you are unsure.",
            body,
        ),
        Spacer(1, 16),
        Paragraph("Checklist", styles["Heading2"]),
    ]
    for d in docs:
        star = " <b>*</b>" if d.mandatory else ""
        scan = " <b>(SCAN)</b>" if d.needs_scan else ""
        story.append(
            Paragraph(f"&#9744; {_xml(d.label)}{star}{scan}", body)
        )
    story.append(Spacer(1, 10))

    for d in docs:
        flags = (" *" if d.mandatory else "") + (
            "  (SCAN REQUIRED)" if d.needs_scan else ""
        )
        story.append(Paragraph(_xml(d.label) + _xml(flags), docname))
        story.append(
            Paragraph(_xml(_BODY.get(d.key, "(specification pending)")), mono)
        )

    pdf.build(story)
    return f"{_slug(client_name)}-document-request.pdf", buf.getvalue()
