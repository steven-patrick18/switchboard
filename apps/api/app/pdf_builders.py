"""Per-filing PDF builders.

Each approved filing's executor calls one of these to render an actual
mailable PDF from the structured payload, instead of leaving the
Document Hub with a metadata-only stub. The PDFs are content-addressed
in `app/storage.py` and linked to the Approval row so the email-packet
send path can attach them.

These are NOT pixel-perfect facsimiles of the agency's official form
— that's a follow-up phase. They're cleanly-formatted printable
representations of the data, ready for the operator to mail/upload OR
the agency to acknowledge by docket. Each one has a header, a labelled
data section, the LOA-style signature block, and a footer crediting
Switchboard as the filing agent.
"""

from __future__ import annotations

from io import BytesIO


def _xml(text: object) -> str:
    """Escape arbitrary values for ReportLab Paragraph markup. Preserves
    line breaks. Coerces None to '—' so missing fields render as the
    standard placeholder rather than the literal string 'None'."""
    if text is None or text == "":
        return "—"
    s = str(text)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def _fmt_address(addr: object) -> str:
    """Render principal_business_address (dict) into a single string."""
    if not isinstance(addr, dict):
        return "—"
    parts = [
        addr.get("street"),
        addr.get("city"),
        addr.get("state"),
        addr.get("zip"),
    ]
    return ", ".join(str(p) for p in parts if p)


def _build_pdf(title: str, story_builder) -> bytes:
    """Common scaffolding — letter page size, sensible margins, title +
    a footer credit. `story_builder(story, styles)` appends the
    per-form content; this wrapper handles the boilerplate."""
    from reportlab.lib.enums import TA_LEFT  # noqa: PLC0415
    from reportlab.lib.pagesizes import letter  # noqa: PLC0415
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: PLC0415
    from reportlab.lib.units import inch  # noqa: PLC0415
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer  # noqa: PLC0415

    styles = getSampleStyleSheet()
    label = ParagraphStyle(
        "Label",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=11,
        textColor="#475569",
        alignment=TA_LEFT,
        spaceBefore=8,
    )
    styles.add(label)
    value = ParagraphStyle(
        "Value",
        parent=styles["Normal"],
        fontSize=11,
        leading=14,
        spaceAfter=4,
    )
    styles.add(value)

    buf = BytesIO()
    pdf = SimpleDocTemplate(
        buf,
        pagesize=letter,
        title=title,
        leftMargin=0.9 * inch,
        rightMargin=0.9 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.8 * inch,
    )
    story: list = [
        Paragraph(_xml(title), styles["Title"]),
        Paragraph(
            "Switchboard · The AI Operator Platform", styles["Italic"]
        ),
        Spacer(1, 16),
    ]
    story_builder(story, styles)
    story.append(Spacer(1, 24))
    story.append(
        Paragraph(
            "<i>Filed by Switchboard on behalf of the applicant. "
            "Generated programmatically from the platform's intake "
            "record and CORES lookup.</i>",
            styles["Normal"],
        )
    )
    pdf.build(story)
    return buf.getvalue()


# --- NECA-OCN-2 ----------------------------------------------------


def build_neca_ocn_2_pdf(payload: dict, legal_name: str | None) -> bytes:
    """Render the NECA-OCN-2 application from the approved payload.
    `payload` mirrors what the carrier agent queued (see
    app/agents/carrier.py)."""
    from reportlab.platypus import Paragraph, Spacer  # noqa: PLC0415

    payload = payload or {}

    def builder(story, styles):
        def field(lbl: str, val: object) -> None:
            story.append(Paragraph(_xml(lbl), styles["Label"]))
            story.append(Paragraph(_xml(val), styles["Value"]))

        story.append(Paragraph("Applicant", styles["Heading2"]))
        field("Legal name", payload.get("applicant_legal_name") or legal_name)
        field("Doing business as", payload.get("doing_business_as"))
        field("Entity type", payload.get("entity_type"))
        field("Formation state", payload.get("formation_state"))
        field("EIN", payload.get("ein"))
        field("FRN", payload.get("frn"))
        field(
            "Principal business address",
            _fmt_address(payload.get("principal_business_address")),
        )

        story.append(Spacer(1, 12))
        story.append(Paragraph("Service request", styles["Heading2"]))
        field("Company type requested", payload.get("company_type_requested"))
        states = payload.get("service_area_states") or []
        if isinstance(states, list):
            field("Service area states", ", ".join(str(s) for s in states))
        else:
            field("Service area states", states)
        field(
            "Intends international",
            "Yes" if payload.get("intends_international") else "No",
        )
        field("Requested OCN block size", payload.get("ocn_block_size_requested"))
        field("Requested OCN block quantity", payload.get("requested_ocn_block_quantity"))
        field("Effective date requested", payload.get("effective_date_requested"))

        story.append(Spacer(1, 12))
        story.append(Paragraph("Authorized officer", styles["Heading2"]))
        officer = payload.get("authorized_officer") or {}
        field("Name", officer.get("name"))
        field("Title", officer.get("title"))
        field("Email", officer.get("email"))

        story.append(Spacer(1, 12))
        story.append(Paragraph("Operational contact", styles["Heading2"]))
        contact = payload.get("primary_operational_contact") or {}
        field("Name", contact.get("name"))
        field("Email", contact.get("email"))
        field("Phone", contact.get("phone"))

        cert = payload.get("certification") or {}
        if cert:
            story.append(Spacer(1, 12))
            story.append(Paragraph("Certification", styles["Heading2"]))
            field("Signed by", cert.get("name") or officer.get("name"))
            field("Title", cert.get("title") or officer.get("title"))
            field("Date", cert.get("date"))

    return _build_pdf("NECA Form OCN-2 — Operating Company Number Application", builder)


# --- Letter of Agency ----------------------------------------------


def build_letter_of_agency_pdf(payload: dict, legal_name: str | None) -> bytes:
    """Render the LOA from the approved NECA-OCN-2 payload's nested
    `letter_of_agency` block (or a standalone LOA payload)."""
    from reportlab.platypus import Paragraph, Spacer  # noqa: PLC0415

    payload = payload or {}
    loa = payload.get("letter_of_agency") or payload

    def builder(story, styles):
        def field(lbl: str, val: object) -> None:
            story.append(Paragraph(_xml(lbl), styles["Label"]))
            story.append(Paragraph(_xml(val), styles["Value"]))

        story.append(Paragraph("Parties", styles["Heading2"]))
        field("Grantor", loa.get("grantor") or legal_name)
        field("Grantee (filing agent)", loa.get("grantee") or "Switchboard")

        story.append(Spacer(1, 12))
        story.append(Paragraph("Scope of authority", styles["Heading2"]))
        scope = loa.get("scope") or [
            "Prepare, sign, and submit NECA Company Code (OCN) "
            "application and related correspondence on behalf of the "
            "Grantor.",
            "Receive NECA notices and assigned OCN identifiers.",
            "Coordinate downstream LERG / carrier notifications.",
        ]
        if isinstance(scope, list):
            for s in scope:
                story.append(Paragraph("• " + _xml(s), styles["Value"]))
        else:
            story.append(Paragraph(_xml(scope), styles["Value"]))

        story.append(Spacer(1, 12))
        story.append(Paragraph("Effective", styles["Heading2"]))
        field("Effective date", loa.get("effective_date"))
        field("Expiration", loa.get("expiration") or "Until revoked in writing")

        story.append(Spacer(1, 24))
        story.append(Paragraph("Authorization", styles["Heading2"]))
        field("Signatory name", loa.get("signatory_name"))
        field("Signatory title", loa.get("signatory_title"))
        field("Signatory email", loa.get("signatory_email"))
        field("Signature date", loa.get("signature_date"))
        story.append(Spacer(1, 24))
        story.append(
            Paragraph(
                "Signature: ______________________________________________",
                styles["Value"],
            )
        )

    return _build_pdf("Letter of Agency", builder)


# --- Generic fallback ----------------------------------------------


def build_generic_filing_pdf(form: str, payload: dict, legal_name: str | None) -> bytes:
    """For filings without a dedicated builder (FCC 499, RMD, etc.) —
    dump the structured payload as a labelled key/value table so the
    operator always has SOMETHING printable to attach."""
    from reportlab.platypus import Paragraph, Spacer  # noqa: PLC0415

    payload = payload or {}

    def builder(story, styles):
        def field(lbl: str, val: object) -> None:
            story.append(Paragraph(_xml(lbl), styles["Label"]))
            story.append(Paragraph(_xml(val), styles["Value"]))

        field("Applicant", legal_name)
        field("Form", form)
        story.append(Spacer(1, 12))
        story.append(Paragraph("Form data", styles["Heading2"]))
        for key, val in payload.items():
            if isinstance(val, (dict, list)):
                import json  # noqa: PLC0415

                field(key, json.dumps(val, indent=2))
            else:
                field(key, val)

    return _build_pdf(f"{form} — filing", builder)
