"""Per-form email packet templates.

Each approved filing's executor calls `build_email_packet(...)` to
produce a structured `EmailPacket` (recipient, subject, body). The
operator sees the prefilled email on the approval card and can either:
  - One-click send via SMTP (if configured), or
  - Copy-paste into their own email client.

Recipients are the well-known destinations for each filing:
  - NECA-OCN-2 -> ocn-admin@neca.org (NECA's OCN administration)
  - FCC 499-A/Q -> usac uploads via portal, but a confirmation copy can
    go to the USAC helpline
  - State CPCNs -> per-state PUC filing email (case-by-case)
  - Carrier LOAs -> the carrier's regulatory team

The catalog is intentionally small and explicit; novel forms fall
back to a generic 'review-needed' packet so the operator always has
SOMETHING to act on even if the form isn't in the table.
"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class EmailPacket:
    """A ready-to-send email. `body` is plain text (markdown-style is
    fine — operators see it in a <pre> block). The optional `cc` and
    `attachments_note` are advisory hints the operator can act on."""

    to: str
    from_address: str
    subject: str
    body: str
    cc: list[str] | None = None
    attachments_note: str | None = None

    def to_json(self) -> dict:
        return asdict(self)


# Per-form template hooks. Each function receives the executor's
# context (the form name + payload + client) and returns a packet.

_NECA_OCN_BODY = """\
Dear NECA OCN Administration,

Please find attached the NECA-OCN-2 application + signed Letter of
Agency for {legal_name} ({ein}).

The applicant is a Competitive Local Exchange Carrier seeking an
Operating Company Number for wholesale voice interconnection.

Please reply to this email with the assigned OCN, or contact us if
any clarification is needed.

Thank you,
{from_address}
(Authorized agent for {legal_name})
"""


_FCC_499_BODY = """\
USAC — please find attached our FCC Form 499-{form_variant} filing
for {legal_name} ({ein}), FRN {frn}.

Form was completed via Switchboard. Officer certification under
penalty of perjury included.

Reply confirming receipt or with any deficiency notice.

Thank you,
{from_address}
"""


_RMD_BODY = """\
FCC Robocall Mitigation Database team,

Attached: RMD entry for {legal_name} (OCN {ocn}, FRN {frn}). Mitigation
plan and STIR/SHAKEN status as specified.

Confirm filing acceptance at your convenience.

Thank you,
{from_address}
"""


_STATE_CPCN_BODY = """\
{state_name} Public Utility Commission,

Please find attached the CPCN application packet for {legal_name}
({ein}), seeking authority to provide intrastate
telecommunications services.

Officer certifications, financial showing, and required attachments
are included. Notarized originals will follow by certified mail per
your filing requirements.

Reply to confirm receipt and assign a docket number.

Thank you,
{from_address}
"""


_GENERIC_BODY = """\
Please find attached: {summary}.

Submitted on behalf of {legal_name} ({ein}).

Reply to this email with any clarification requests.

Thank you,
{from_address}
"""


def _safe(value: str | None, fallback: str = "[TBD]") -> str:
    if not value:
        return fallback
    return str(value).strip() or fallback


def build_email_packet(
    *,
    form: str,
    payload: dict | None,
    legal_name: str | None,
    ein: str | None,
    from_address: str,
    summary: str | None = None,
) -> EmailPacket:
    """Return a prefilled EmailPacket for this form. `form` matches the
    filing identifier (e.g. 'NECA-OCN-2', 'FCC 499-A'). Unknown forms
    get a generic packet so the operator always has something to send."""

    legal_name = _safe(legal_name, "[client legal name TBD]")
    ein = _safe(ein, "[EIN TBD]")
    payload = payload or {}

    fkey = (form or "").strip().lower()

    if fkey.startswith("neca-ocn"):
        body = _NECA_OCN_BODY.format(
            legal_name=legal_name, ein=ein, from_address=from_address
        )
        return EmailPacket(
            to="ocn-admin@neca.org",
            from_address=from_address,
            subject=f"NECA-OCN-2 application: {legal_name} (EIN {ein})",
            body=body,
            cc=None,
            attachments_note=(
                "Attach: NECA-OCN-2.pdf, signed Letter of Agency, "
                "FCC CORES FRN confirmation."
            ),
        )

    if fkey.startswith("fcc 499") or fkey.startswith("fcc_499") or fkey.startswith("499"):
        variant = "A"
        if "q" in fkey:
            variant = "Q"
        frn = _safe(payload.get("frn") or payload.get("fields", {}).get("frn"))
        body = _FCC_499_BODY.format(
            form_variant=variant,
            legal_name=legal_name,
            ein=ein,
            frn=frn,
            from_address=from_address,
        )
        return EmailPacket(
            to="filer-help@usac.org",
            from_address=from_address,
            subject=f"FCC Form 499-{variant} filing: {legal_name} (FRN {frn})",
            body=body,
            attachments_note="Attach: completed Form 499-{variant}.pdf, officer cert.".format(
                variant=variant
            ),
        )

    if fkey.startswith("rmd") or "robocall" in fkey:
        frn = _safe(payload.get("frn"))
        ocn = _safe(payload.get("ocn"))
        body = _RMD_BODY.format(
            legal_name=legal_name,
            frn=frn,
            ocn=ocn,
            from_address=from_address,
        )
        return EmailPacket(
            to="rmd-help@fcc.gov",
            from_address=from_address,
            subject=f"RMD entry: {legal_name} (OCN {ocn})",
            body=body,
            attachments_note="Attach: RMD entry PDF, mitigation plan.",
        )

    if fkey.startswith("state_cpcn") or fkey.startswith("cpcn"):
        # state_cpcn:XX form names — pull state code
        state = ""
        if ":" in fkey:
            state = fkey.split(":", 1)[1].upper()
        state_name = state or "State"
        # Per-state inboxes are case-by-case; left as TBD here so the
        # operator types the live one for their target state.
        body = _STATE_CPCN_BODY.format(
            state_name=state_name,
            legal_name=legal_name,
            ein=ein,
            from_address=from_address,
        )
        return EmailPacket(
            to=f"[{state_name} PUC filing email — TBD]",
            from_address=from_address,
            subject=f"CPCN application: {legal_name} ({state_name})",
            body=body,
            attachments_note=(
                "Attach: CPCN application packet, officer certs, "
                "financial showing, surety bond if required."
            ),
        )

    # Fallback for novel filing types.
    body = _GENERIC_BODY.format(
        summary=_safe(summary, "the attached filing"),
        legal_name=legal_name,
        ein=ein,
        from_address=from_address,
    )
    return EmailPacket(
        to="[recipient email — TBD]",
        from_address=from_address,
        subject=f"{form}: {legal_name}",
        body=body,
        attachments_note="Attach: the filing artifact from the Document Hub.",
    )
