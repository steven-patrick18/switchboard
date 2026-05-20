# First Client — End-to-End Walkthrough

You've deployed Switchboard (see [DEPLOY.md](DEPLOY.md)) and your
Settings page shows **Anthropic API key: ready**. Now you're going
to walk a real client through launching a US VoIP company. This
doc is the order of operations.

> The honest scope today: Switchboard **drafts and tracks** every
> regulated filing, queues each external action for your approval,
> and audits the trail. The actual submission step (clicking
> "submit" on the FCC portal, mailing a notarized state CPCN,
> emailing NECA the OCN form) is still **you** doing it on the
> real portal — you mark the filing as `submitted` in Switchboard
> after you do. When the Playwright integrations land, that final
> step also moves into the platform; nothing else about the flow
> changes.

---

## Phase 0 — Before the kickoff call (5 min)

In Switchboard:

1. **Workspaces** → **Add client** → put the company name
   (use whatever the client calls themselves; the legal name
   gets captured later).
2. Open the client page.
3. **Client share links** → **Generate link** with a label
   like "Onboarding · {client first name}". Copy the URL.
4. Send it to the client (email, text, Signal — whatever).

The client opens that URL and sees a clean form just for them
(no operator nav, no other clients). They can fill the intake,
upload docs, and drop in portal credentials — all scoped to
their own company.

---

## Phase 1 — Capture-once intake (client does this; ~30 min)

The client fills the **Company details** form on their portal page:

- Legal name, entity type, formation state, EIN, OCN (if they
  already have one — most won't)
- Principal business address
- Signing officer name + title + email
- Primary contact (you / their COO)
- Target states they want to provide service in
- Whether they intend international service
- Estimated monthly revenue (for FCC 499 tiering)

Then they upload the mandated documents (the required-doc list at
the top of their portal shows exactly what — SSN card, driver's
license, founder photo, utility bill while pre-formation; EIN
letter + formation certificate + officer ID + bank letter +
notarized state CPCN packet once the entity is formed).

If the client doesn't have something yet (very common: EIN
takes ~10 minutes to get from the IRS website during business
hours), they upload what they have and come back to the link
later.

**What you do in parallel**: keep the client page open on your
side. You see uploads appear in real time. Every action on the
portal audits as `client:{link.id}` so your trail explains
exactly who did what.

---

## Phase 2 — Sync the launch checklist (you, 30 sec)

Once intake has at least the EIN captured:

1. On the client page → **Launch progress** → **Sync from intake**.
2. Switchboard auto-creates the application rows the intake mandates:
   - **OCN (NECA)** — Operating Company Number application
   - **FCC Form 499-A** — filer registration
   - **Robocall Mitigation Database** — RMD entry
   - **STIR/SHAKEN token** — STI-PA certificate
   - **State CPCN** — one row per target state
   - **Section 214** — if international
   - Plus entity_formation / ein / bank_account (already done
     if they got that far)

You now have a checklist of every regulated filing this launch needs.

---

## Phase 3 — Let the agents draft (you, 5 min of clicks)

For each application, kick off an agent:

| Application | Run this agent | Why |
|---|---|---|
| OCN | `carrier` | "Draft the NECA-OCN-2 application and LOA template." |
| FCC 499-A | `compliance` | "Draft FCC Form 499-A using the captured intake." |
| RMD | `compliance` | "Draft the Robocall Mitigation Plan." |
| STIR/SHAKEN | `carrier` | "Lay out the STI-PA token application sequence." |
| state_cpcn:TX | `state_licensing` | "Draft Texas SPCOA application + LOA." |
| (repeat per state) | `state_licensing` | One agent run per target state. |

In the client page → **Run an agent** → pick the agent → instruction
→ **Run**. The agent reads the intake + past corrections + tool
catalog, produces a draft, and (for anything that would actually
file with the FCC, NECA, a state PUC, or a carrier portal) queues
a tier-2 or tier-3 **Approval** that lands in your queue.

**Agents will use `update_application_stage`** as they work, so
the Launch progress section shows real-time:
- `OCN — in progress — carrier working`
- `FCC 499-A — awaiting approval — compliance working`
- `state_cpcn:TX — in progress — state_licensing working`

---

## Phase 4 — Review the queue (you, 30 min)

Open **Approval queue**. You'll see one card per gated action with:
- The client name + tier pill (T2/T3)
- The exact payload the agent prepared (JSON)
- Three buttons: **Approve**, **Edit & approve**, **Reject**

The decision is yours. For each card:

- **Looks right** → Approve. Switchboard records the decision in
  the audit log; the application stage moves to `submitted` (you
  flip that yourself once you actually file it on the real portal —
  see Phase 5).
- **Almost right** → Edit & approve. You can fix the payload
  directly. **Switchboard captures the diff as a lesson for that
  agent** — the next time it drafts the same filing for the same
  operator, it sees "operator changed field X from A to B" in its
  prompt and starts producing that shape on its own.
- **Wrong** → Reject. You **must** type a reason (the schema
  rejects an empty reason). Same lesson capture — the agent reads
  "operator REJECTED this for: <your reason>" on every future run.

This is the **learning loop**. After 5-10 corrections per agent,
their drafts get significantly tighter. Check the agent's lesson
count on the **AI Agents** page; you can also add manual lessons
("always cite FCC rule by section number") that inject the same
way.

---

## Phase 5 — Actually file (you, real-world step)

For each approved application, do the external filing on the real
portal:

- **OCN** → upload Form NECA-OCN-2 + LOA at
  `neca.org/business-solutions/companycodeocnadministration`.
  Pay the fee. Save the acknowledgment.
- **FCC 499-A / RMD** → USAC E-File at `efile.usac.org`.
- **STIR/SHAKEN** → STI-PA at `authenticate.iconectiv.com`.
- **State CPCNs** → each state PUC has its own portal; the
  state_licensing agent's draft + LOA tells you which.
- **Carrier signups** → carrier portals (Twilio, Bandwidth, etc.).

Back in Switchboard, on each application row:
1. Change the **stage** to `submitted`.
2. Click **edit** and paste the external reference (filer ID,
   NECA OCN code, docket number) into **External reference**.
3. (Optional) Drop a note like "Filed 5/20, NECA confirmation
   received via email."

The audit log captures the transition; the Launch progress card
now shows your filing ID alongside the stage chip.

---

## Phase 6 — Wait + track + advance (~weeks)

OCN takes 2-3 weeks. STIR/SHAKEN 4-8 weeks. State CPCNs 2-12 weeks
depending on the state. While you wait:

- Set the stage to `under_review` on each filing.
- When the FCC / state / NECA / carrier replies, update notes
  with what they said and (if needed) flip the stage to `blocked`
  with the operator's note explaining what they need from the
  client.
- The agent learning system means: every time you reject or edit
  follow-up communications, the agents get sharper.

---

## Phase 7 — Go-live (~last week)

When OCN + RMD + STIR/SHAKEN + at least one state CPCN + at least
one carrier interconnect are all `complete`, you can flip each
final carrier-related application to live numbers. Test calls.
Monitor. The client now has a working US VoIP company.

Mark the final application rows `complete`. Hit **Archive ↓** at
the top of the client page to download a zip containing:
- intake.json — the captured intake
- audit.csv — the full trail of every operator + agent + client
  action across the entire launch
- documents/ — every uploaded file, latest version of each type

That's your compliance archive — keep it in case anyone ever asks.

---

## What to do when an agent gets something wrong

Reject the approval **with a specific reason**. Don't say "no";
say *why* and *what to do instead*. The reason is what the agent
sees on its next run. Examples that work:

- ❌ Bad: "Wrong tone."
- ✅ Good: "Use the legal entity name (Acme VoIP LLC), not the
  DBA (Acme Talk). The FCC matches against the EIN-registered name."

- ❌ Bad: "Wrong format."
- ✅ Good: "FCC 499 expects revenue in thousands, not raw dollars.
  Divide by 1000 before submitting."

The audit log captures both the agent's original output AND your
correction, so when you onboard another client six months later
the same agent already knows the rules you taught it.

---

## Cheat sheet: which agent owns what

| You're working on... | Agent | Tools they use |
|---|---|---|
| What is the client still missing? | `intake` | check_intake_status, draft_client_email |
| Federal FCC anything | `compliance` | lookup_fcc_requirement, queue_filing_submission |
| State-specific CPCN / PUC | `state_licensing` | lookup_state_requirement, queue_filing_submission |
| OCN, carrier interconnect, STIR/SHAKEN | `carrier` | lookup_carrier_specs, request_portal_action |
| MSAs, LOAs, contract drafts | `document` | lookup_document_template, send_document_for_signature |
| Big-picture sequencing | `pm` | assign_task (delegates to the others) |

If you find yourself wanting an agent that doesn't exist (e.g. a
`billing` agent for monthly carrier reconciliation), open the **AI
Agents** page → **+ New agent** → write its system prompt → pick
its tools from the catalog. No code needed.
