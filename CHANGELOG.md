# Changelog

All notable changes to Switchboard. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions
are major milestones, not formal releases — every push to `main`
auto-deploys to Railway, so the commit hash is the real version
identifier (see Settings → Platform readiness in the running app).

## v1.3.12 — Agents read real intake values instead of placeholders · 2026-05-20

### Fixed — drafts no longer say `[from intake]` for every field
Looking at the v1.3.11 carrier NECA-OCN-2 draft on Amber Sidney
Hunt's approval queue, every field except the explicit TBDs was
also a placeholder: `"applicant_legal_name": "[from client intake
— operator confirm]"`, `"dba": "[from client intake]"`, etc. Root
cause: the filing-drafting agents (carrier, compliance, etc.)
had **no tool to read the actual intake field values**. The
existing `check_intake_status` returns completeness ("missing
fields: X, Y") but never the values themselves. So the agent
correctly knew the values existed but couldn't quote them.

### Added — `read_client_intake` tool (T0, read-only)
Returns the captured intake as a structured JSON blob: legal_name,
entity_type, formation_state, ein, principal_address, officer_*,
primary_contact_*, target_states, intends_international,
estimated_monthly_revenue, ocn, extra. Wired into:

- **carrier** — prompt now reads `read_client_intake` as **step 1**
  of the OCN drafting flow, then `lookup_frn` as step 2, then
  drafts NECA-OCN-2 + LOA with real values, then queues T3.
- **compliance** — prompt says "Before drafting ANY filing, call
  read_client_intake."
- **state_licensing** — same: call it before any state CPCN draft.
- **document** — same: real values in TOS / AUP / LOA drafts.
- **intake** — has it too (read-then-decide patterns).

The prompts all explicitly forbid `[from intake]` placeholders
for fields the tool returned.

### Tests (`tests/test_read_client_intake.py`)
- Tool returns real values (legal_name, ein, officer email,
  target_states) for a seeded client.
- Brand-new client with no intake row → plain-English message,
  not a traceback.
- No client context → safe degradation.
- Registered in `ALL_TOOLS` so the GUI agent-builder catalog
  sees it.
- All five filing-drafting agents have it in their toolset.
- Carrier prompt regression: explicitly references
  `read_client_intake`, "FIRST", `lookup_frn`, BIAS TOWARD ACTION.

135 tests passing.

### Immediate workaround for the current pending OCN approval
Until the new deploy lands, the approval card you have open is in
edit mode — you can just edit the JSON directly: replace each
`"[from client intake]"` string with the actual value from the
client's intake page, replace `"frn": "[TBD …]"` with the real
10-digit FRN, and click **Save & approve**. That records as
`edited` (vs `approved`) and audits the diff. Future drafts after
this deploy will arrive with real values inline.

## v1.3.11 — Carrier agent looks up the FRN itself · 2026-05-20

### Fixed — carrier agent no longer asks the operator for the FRN
Last round (v1.3.9 / v1.3.10) made the carrier agent always queue
the NECA-OCN-2 draft with `[FRN: TBD]` for the operator to fill in.
That solved the stalling problem but missed the cleaner answer:
**the FCC CORES credential is already in the vault, the agent
already has `request_portal_action` in its toolset, and in
autonomous mode T2 actions auto-execute**. The agent should look
up the FRN itself instead of asking.

The gap was in the portal-action catalog: `check_filer_status`
needs you to already know the FRN, and `register_filer_id` is for
brand-new entities. There was no "look up the FRN we have on file"
action.

New `fcc_cores:lookup_frn` portal action (T2, read-only):
- params: `legal_name`, `ein` (both required)
- returns: 10-digit FRN + CORES status

Demo backend handler returns a deterministic synthetic FRN derived
from the EIN digits — re-runs are idempotent, and when the real
Playwright/HTTP backend lands later the shape is unchanged so the
carrier agent + executor + audit trail need zero changes.

### Carrier agent prompt — explicit lookup_frn-first workflow
The carrier prompt now spells out a 4-step OCN drafting flow:
1. If the FRN is needed and unknown, call
   `request_portal_action(service='fcc_cores',
   action='lookup_frn', params={legal_name, ein})` first. In
   autonomous mode this auto-executes; in supervised it queues a
   quick T2. Use the returned FRN.
2. Produce the full NECA-OCN-2 + LOA package using intake +
   looked-up FRN.
3. Only mark as `[TBD]` the things you genuinely cannot read
   (e.g. a requested OCN block range).
4. ALWAYS call `queue_filing_submission` for the T3 approval.

### Tests (`tests/test_lookup_frn.py`)
- Catalog entry exists at tier T2 with the right required params.
- `missing_params` correctly flags missing legal_name / ein.
- Demo handler returns `FOUND` with a 10-digit FRN derived from
  EIN and is deterministic across calls.
- Missing-params path returns `MISSING_PARAMS` with the list.
- Regression: carrier prompt explicitly mentions `lookup_frn`,
  `fcc_cores`, BIAS TOWARD ACTION, and the never-punt rule.

129 tests passing.

No migration. Once deployed, hitting Start ▸ on OCN with autonomy
on Amano Telecom will: auto-execute `lookup_frn` against CORES →
read the real FRN back → produce the NECA-OCN-2 package with the
real FRN inline → queue T3 approval for your sign-off.

## v1.3.10 — Tighter correction loop + stale Live-Activity fix · 2026-05-20

### Fixed — Task close-out (Live Activity no longer shows phantom rows)
When an operator decided the LAST pending approval on a task
(approve, edit-and-approve, or reject), the parent `Task.status`
stayed at `awaiting_approval` forever. The Live Activity feed
filters by exactly that status, so completed work kept showing
as "awaiting your approval" — that's what produced the
red-boxed phantom row on Amber Sidney Hunt's activity page.

Fix: `_close_task_if_done(task_id, db)` runs after every
approve / reject / batch / send-back decision. It scans for any
remaining pending approvals on the same task; if none, the task
moves to `completed`. Tightly scoped — only flips from
`awaiting_approval` (never from `running` / `queued`) so it
cannot race with a still-running agent loop.

### Added — `POST /approvals/{id}/send-back` (one-click correction)
The original loop was "this approval looks wrong → Reject with a
reason → manually click Start ▸ again on the client page → agent
reruns and produces a fresh draft." That's three steps and a
context switch. New endpoint collapses it to one:

1. Operator clicks **Send back to agent** on the approval card
2. Types what needs to change ("FRN should be 0001234567 — fill
   it in instead of TBD")
3. Submit

Backend:
- The current approval is rejected with the feedback as the
  captured reason (so it ALSO writes an AgentLesson — the agent
  learns from the correction on every subsequent run, not just
  this one).
- A new task is created on the same project, same agent, with
  `instruction = original + "OPERATOR FEEDBACK: …" + "redo with
  that change"`.
- If `ANTHROPIC_API_KEY` is configured the agent runs in-line and
  returns the new approval id(s). If not (or the run throws), the
  new task lands as `queued` and the operator clicks Start ▸ when
  ready. Either way the response tells the UI exactly what
  happened.
- Audited as `approval.sent_back` (distinct from `approval.rejected`)
  so the history view can show "operator sent this back" vs.
  "operator dropped this entirely."

### Frontend (`apps/web/app/approvals/ApprovalCard.tsx`)
- New **Send back to agent** button next to Approve / Edit / Reject.
- Clicking it opens an inline indigo panel with a textarea and a
  one-line explanation of what will happen.
- Result panel after submit: "Agent re-ran. N new approval(s)
  queued" or "Sent back — new task queued. Click Start ▸ on the
  client page to run it" — collapsible "Show agent reply" if the
  agent ran inline.

### Tests (`tests/test_approval_close_out.py`)
- Two-approval task: approve one → task stays `awaiting_approval`;
  reject the other → task moves to `completed`. Locks in the
  bug fix.
- Send-back with no Anthropic key: original approval rejected with
  feedback as reason, new task queued with the feedback inside
  the instruction, parent task closes. 124 tests passing.
- Blank-feedback send-back returns 422.

No migration. The Anthropic key is read at send-back time so an
operator on a key-less workspace can still queue the redo and
hit Start themselves.

## v1.3.9 — Carrier agent stops stalling on OCN · 2026-05-20

### Fixed — carrier agent now always queues the NECA-OCN-2 draft
After v1.3.8 flipped Amber Sidney Hunt (Amano Telecom) into
autonomous mode, hitting **Start ▸** on OCN ran the carrier agent
to completion with **0 approvals queued**. The agent's reply was
reasonable but unhelpful — it said "confirm the FRN, then I (or
compliance) can verify it before we mail NECA" and stopped.

Two real problems in the prompt:
1. The carrier agent stalled on a missing field (FRN) instead of
   producing the package with a `[FRN: TBD]` placeholder and
   letting the operator fill it in at approval time. The
   edit-before-approve path already exists in the approval queue.
2. The agent kept hedging with "(or compliance)" for CORES /
   NECA work — even though the v1.3.6 prompt told it OCN is its
   job, not compliance's.

Now (v1.3.9):
- Prompt has a top-level **"BIAS TOWARD ACTION"** section telling
  the carrier agent to ALWAYS call `queue_filing_submission` when
  asked for a draft; mark unknown fields with `[FRN: TBD]` etc.
  and let the operator edit before approve.
- Explicit "NEVER suggest a different agent take over" — kills
  the residual compliance-hedge.
- Clarifies that T3 always queues regardless of autonomy mode —
  so the agent should just call the tool rather than think it
  through.
- Test `test_carrier_agent_has_ocn_queueing_tool` extended to
  pin the new prompt language (`bias toward action`, `tbd`,
  `never suggest a different agent`) so we cannot regress.

No migration; no schema change. Restart the API and the next time
you click **Start ▸** on OCN you get a queued NECA-OCN-2 +
Letter of Agency draft, ready for you to edit any TBD fields and
approve.

## v1.3.8 — Per-client autonomous mode (~85% hands-off) · 2026-05-20

### Added — `autonomy_level` per client: supervised | autonomous
Until now every regulated/external action queued for operator
approval — both T2 (carrier portals, client emails, internal
look-ups) and T3 (FCC filings under penalty of perjury,
e-signatures). That's safe but high-friction once you trust the
agent loop.

New per-client `autonomy_level` setting on the **client page** lets
you opt in to a hands-off mode:

- **`supervised`** (default, original behavior) — every T2 + T3
  action queues. Operator decides each one.
- **`autonomous`** — T2 actions auto-execute. An `Approval` row
  is still created (`decision='approved'`, `note='autonomous mode'`)
  and the executor runs immediately so the forensic trail is
  intact. **T3 still queues regardless** — that's the legal floor
  (filings under penalty of perjury, e-signatures) and is
  intentionally not bypassable via this flag.

In practice T0 + T1 + T2 ≈ 85% of all tool calls, so flipping a
client to autonomous gets you ~85% hands-off with the legal floor
preserved.

### Surface
- Migration `0015` adds `clients.autonomy_level` (string, default
  `supervised`, NOT NULL).
- `PATCH /clients/{id}` now accepts `autonomy_level`; switching
  modes emits a `client.autonomy_changed` audit row with before
  + after so any change is forensically recorded.
- `run_agent` gains an `autonomy_level` parameter (default
  `supervised`). The agents.py and tasks.py routes thread it
  through from the client row.
- When `autonomy_level=='autonomous'` and a T2 tool fires:
  `Approval(decision=approved, note='autonomous mode')` →
  `execute_approval` runs immediately → audit row
  `approval.auto_executed` (actor: `'{agent} (autonomous)'`).
- T3 path is unchanged — always queues.

### Frontend
- Card at the top of the client page shows the current mode + a
  one-click toggle.
- Switching to autonomous shows a `confirm()` dialog spelling out
  what changes and what stays gated.
- "AUTONOMOUS" pill next to `stage:` in the header when on.

### Tests (`tests/test_autonomy_mode.py`)
- Supervised mode still queues T2 (regression baseline).
- Autonomous mode T2 → executes + Approval row recorded as
  approved + audit row `approval.auto_executed`.
- Autonomous mode T3 → still pending. Hard assertion that the
  legal floor holds.

121 tests passing.

## v1.3.7 — Live activity page · 2026-05-20

### Added — Cross-client live view of every agent
The operator could see what one client's agents were doing on that
client's page, but there was no single place to answer "what is
every one of my agents doing right now?" — especially when more
than one client is mid-launch.

New page **/activity** in the sidebar (between Approval queue and
History). Polls every 5 seconds and shows four feeds:

1. **Running now** — tasks where `status='running'` (an agent loop
   is actively executing tools). Links straight through to the
   client page.
2. **Awaiting your approval** — tasks paused with one or more
   queued approvals. Each row has a direct link to the queue.
3. **Who's working on what** — application rows an agent has
   claimed via `update_application_stage`. Pulled from
   `Application.current_agent` + `stage in {in_progress,
   awaiting_approval, blocked}`. Shows the self-reported note so
   you can see, e.g., "carrier is drafting NECA-OCN-2 with the
   attached LOA."
4. **Recent agent runs** — last 30 `AgentRun` rows operator-wide,
   with duration, tokens in/out, and per-run spend.

Six headline tiles at the top: running, awaiting approval, active
assignments, runs in 24h, tokens in 24h, cost in 24h.

Backend: new `GET /activity` route (`app/api/routes/activity.py`),
operator-scoped (filters every section by `Client.owner_id`). Two
tests in `test_activity.py` lock in the contract: shape +
operator isolation.

Pause/Resume button on the page so the operator can freeze the
view (e.g. to copy a note out). Pause stops the polling timer;
Resume restarts it on the next tick.

No migration. The data already existed — this page just makes it
visible at the operator level.

## v1.3.6 — Carrier agent owns OCN end-to-end · 2026-05-20

### Fixed — Carrier agent no longer punts OCN to compliance
While onboarding the first real client (Amano Telecom LLC), clicking
**Start ▸** on the OCN row launched the carrier agent — which then
replied "reassign the OCN application to the compliance agent." That
is wrong on two counts:

1. **OCN is administered by NECA, not the FCC.** The compliance
   agent's scope is strictly federal FCC filings (CORES, 499, RMD,
   Section 214). It must not touch OCN.
2. **The platform's catalog already maps `ocn` → carrier** (see
   `app/applications.py`). The agent was contradicting its own
   assignment.

The bug was twofold:
- The carrier system prompt mentioned OCN only as a *prerequisite* —
  never as a deliverable the carrier agent owns.
- The carrier agent's toolset did not include `queue_filing_submission`,
  so even if it tried to act it had no way to queue NECA-OCN-2 for
  operator approval. Its only options were prose or carrier-portal
  actions — neither fits a NECA filing.

Now:
- Carrier prompt has an explicit **What belongs to you (do NOT punt
  these elsewhere)** section listing OCN, STIR/SHAKEN, and carrier
  interconnection — and a **What you do NOT own** section pointing
  FCC and state work back to compliance / state_licensing.
- `queue_filing_submission` is added to the carrier agent's tool list
  so it can queue the NECA-OCN-2 package as a tier-3 approval (still
  gated, still audited).
- New regression tests in `tests/test_agents_roster.py`:
  - `test_carrier_agent_has_ocn_queueing_tool` — locks in the tool +
    prompt scoping.
  - `test_carrier_ocn_filing_is_queued_not_executed` — exercises the
    full path: carrier calls `queue_filing_submission` →
    pending Approval row, no external execution.

No model changes; no migration. Restart the API and the next time
you click **Start ▸** on the OCN row, the carrier agent drafts the
NECA-OCN-2 package and queues it for your approval — instead of
hand-waving it back to the wrong agent.

## v1.3.5 — Click a task to read the agent's reply · 2026-05-20

### Added — Expandable task rows
Tasks list previously showed only `{agent} {instruction} {status}`
— the agent's actual reply (stored in `task.output.text`) was
invisible. Operators couldn't see what was drafted without
inspecting the DB.

Now: every task row is clickable. Clicking reveals:
- The full **instruction** sent to the agent
- The full **agent reply** (preformatted text, scrollable up to
  ~96vh)
- **Task ID** + **started timestamp** for grepping logs

If the task hasn't run yet (status = queued) or only used T0/T1
tools without producing a textual summary, the panel says so
plainly instead of being empty.

No backend changes — `TaskOut` already serialized `output: dict |
None`; the frontend just wasn't reading it. The Task TypeScript
type grew the `output` and `created_at` fields to match the
serialized shape.

## v1.3.4 — Inline result panel under Next Steps · 2026-05-20

### Changed — Agent results now show INSIDE the Next Steps section
Previous behaviour: clicking Start on a Ready card kicked off the
agent run, but the success message / error landed in the page-level
message banner at the top of the client page — which the operator
couldn't see if they were scrolled down on the Next Steps section.
Felt like "nothing happened" even when the run had completed.

Now: NextSteps owns its own success / error panel that renders
inline immediately above the Ready list. Operators see:
- **Success**: green panel with "{agent} ran on {label}. N
  approval(s) queued — review them in the Approval queue." plus a
  collapsible "Show agent reply" with the full text, plus a direct
  "Go to Approval queue →" link when approvals were queued.
- **Failure**: red panel with the actual error message + a hint
  pointing the operator to Settings → Platform readiness (most
  common cause: Anthropic key not set) and `docker compose ...
  logs api` for the full trace.
- **Dismiss** link clears the result so the next Start gets a
  fresh slate.

Wired:
- runAgentWith(agent, instruction) now RETURNS the
  { text, approval_ids } shape (was returning void). NextSteps
  captures it and renders inline.
- Error from a failed Start still bubbles up to the page-level
  banner too, but the inline panel is what the operator actually
  sees first.

## v1.3.3 — Credential URL + edit-in-place · 2026-05-20

### Added — URL field on every credential
- Each stored credential now carries an optional **login URL** so
  the agent and operator know exactly where to use the credential
  (especially for domain webmail and custom carrier portals where
  the URL isn't obvious).
- **Known services auto-fill**: if the operator doesn't supply a
  URL but the service name is in the well-known table (`fcc_cores`,
  `usac_efile`, `neca`, `stipa`/`iconectiv`, `rmd`, `irs_eftps`,
  `twilio`, `telnyx`, `bandwidth`, `inteliquent`), the URL gets
  populated automatically — no typing for the obvious cases.
- Each credential row renders an **open ↗** link if a URL is set;
  clicking opens the login page in a new tab.

### Added — Edit existing credentials
- New **edit** button per credential row drops in an inline form
  to update username, URL, or rotate the password — without
  affecting the other fields. Patching is field-level: only the
  keys you send are touched; the rest stay as-is.
- New `PATCH /clients/{id}/credentials/{credential_id}` endpoint
  with the same audit treatment as upsert. The audit log records
  WHICH fields changed (e.g. `{"url": "set", "secret": "rotated"}`)
  but never the secret value itself; a canary test asserts that.
- Owner-scoped — non-owners get 404 on PATCH.

### Wired
- `app/vault.py::update_credential` is the new metadata-only edit
  helper (uses `...` sentinels to distinguish "leave field alone"
  from "clear field to NULL").
- Operator + client-portal credential forms both grew a URL input
  so a magic-link client can include the login URL when uploading
  a portal credential.

113 backend tests pass; tsc clean.

## v1.3.2 — Founder docs go optional + stage-aware required fields · 2026-05-20

### Changed — Founder-stage onboarding is no longer a wall

User feedback from the first real client onboarding: the four
founder-stage documents (SSN card, driver's license, founder photo,
utility bill) were marked mandatory and blocked intake completion.
But the client realistically doesn't have to provide all four — most
are only needed for *specific* downstream steps (SSN for the SS-4
EIN application, DL for a few state-of-formation filings, utility
bill for some bank KYC).

Three coordinated changes so the operator can start processing with
whatever the client provides:

1. **Founder docs → PHASE_LAUNCH** (deferred). They still appear on
   the client page so the operator sees what *may* be useful later,
   but with the "needed at launch" blue badge and "later" status —
   not the red asterisk / "missing" treatment. Intake completeness
   no longer blocks on them.
2. **Stage-aware required intake fields**. A founder-stage client
   (pre-EIN) is only checked against `REQUIRED_FOUNDER_FIELDS` —
   the ten basics needed to drive entity formation (legal_name,
   entity_type, formation_state, principal_address, officer info,
   primary contact info). `ein`, `target_states`, and
   `estimated_monthly_revenue` are deferred to entity stage and only
   become required once the EIN is captured.
3. **Result**: a founder-stage client can be marked `intake ✓
   complete` immediately after capturing the basics, unlocking the
   first applications (entity_formation, ein, bank_account) in the
   Next steps section. When the EIN gets recorded later, the client
   transitions to entity stage and the entity-stage fields + four
   PHASE_INTAKE docs become required for the next round.

Verified live with a fresh founder client (Demo Telecom LLC, no EIN):
intake completed without any docs uploaded, all four founder docs
rendered with the "needed at launch" badge instead of blocking.

### Tests
- `test_doc_requirements` updated: the old "all founder docs
  mandatory" assertion is gone; new tests assert the four are
  PHASE_LAUNCH + `mandatory=False`, intake completes with no docs
  at founder stage, and transitioning to entity stage (EIN
  captured) re-raises the entity-stage requirements.

107 backend tests pass, tsc clean.

## v1.3.1 — "What this agent can do" + per-agent prompt hints · 2026-05-20

### Added — Capability panel under Run-an-agent
- Selecting an agent in the Run-an-agent dropdown now reveals a
  panel listing every tool that agent has access to, with a
  color-coded tier pill (T0 / T1 green = auto-run; T2 amber, T3
  red = queues for your approval) and a one-line description.
- The same panel ends with the legend "T0/T1 run automatically.
  T2/T3 actions are intercepted and queued for your approval —
  they NEVER execute without your sign-off" so a new operator
  immediately understands the safety boundary.

### Added — Per-agent prompt placeholders
- The Instruction field's placeholder text now adapts to the
  selected agent: `compliance` shows "e.g. Draft the FCC 499-A
  based on captured intake", `carrier` shows "Draft the NECA-OCN-2
  application and LOA", `state_licensing` shows "Draft the Texas
  SPCOA application", etc. New operators see the kind of request
  that fits each agent without having to read docs.

### Wired
- The client page now hits `GET /agents/tools` on first load
  (in parallel with `GET /agents`) and caches both into local
  state. No new endpoints; both already existed for the AI Agents
  GUI.

## v1.3.0 — Next steps + readiness agent · 2026-05-20

### Added — "What can we start right now?" answered on every client page
- New **Next steps** section sits above Launch progress on the
  client detail page. Splits every filing into four buckets:
  - **Ready to start** — green cards with one-click Start buttons.
    The platform picks the right agent (compliance for CORES /
    499 / RMD, carrier for OCN / STIR/SHAKEN / carrier MSAs,
    state_licensing for state CPCNs, etc.) and pre-fills a
    templated instruction so the operator doesn't have to think
    about *who* does *what*.
  - **In flight** — blue cards showing stage + which agent is
    working + any operator note.
  - **Blocked** — amber cards listing *exactly* which prereqs
    aren't complete (e.g. "RMD: OCN (NECA) must be complete
    (currently in_progress)").
  - **Complete** — grey check-marks with external_ref numbers.

### Added — Deterministic readiness module + new endpoint
- `app/readiness.py` codifies the dependency graph (CORES →
  OCN → 499 → RMD → STIR/SHAKEN, plus state CPCNs in parallel,
  plus carrier interconnects gated on OCN+STIR/SHAKEN) and the
  per-application templated prompts. Pure Python — no LLM cost,
  no flakiness; the GUI calls it on every page load.
- `GET /clients/{id}/applications/readiness` returns the
  snapshot. Owner-scoped; 404 for non-owners.

### Added — 7th built-in agent: `readiness`
- Tools: `summarize_launch_status` (new, wraps the same compute),
  `check_intake_status`, `update_application_stage`.
- Scope is read-only advice: "given what you've got, here's
  what to do next." Doesn't draft filings, doesn't delegate —
  the operator clicks the Start buttons themselves. Useful as a
  "what's going on?" command for an operator returning to a
  client after a few days.
- Registry now exposes seven agents: pm, readiness, intake,
  compliance, state_licensing, carrier, document.

### Tests
- 6 new readiness tests cover the dependency unlocks
  (CORES → OCN → RMD → STIR/SHAKEN), in-flight items NOT
  appearing as ready, intake-incomplete locking everything,
  and operator-scope isolation.
- Agent roster test bumped to expect the 7th agent.

106 backend tests pass; tsc clean. v1.3.0.

## v1.2.0 — Update + HTTPS + self-test in the GUI · 2026-05-20

### Added — Production-operations from the Settings page
- **System updates** card — shows live commit vs. latest on
  `origin/main` (queried from GitHub's public API), one-click
  **Apply update** writes a sentinel file the host's `update.sh`
  cron picks up on its next tick. **Cancel** unwrites it before
  the cron fires. Audited as `system.update_requested` /
  `system.update_cancelled`.
- **HTTPS certificate** card — connects from the API to its own
  public URL, reads the live cert, surfaces issuer, expiry date,
  days-until-expiry (with green/amber/red pill), covered domains,
  and whether Caddy auto-renews. Confirms TLS health at a glance
  without SSH.
- **System self-test** card — five subsystem checks (database,
  document storage, Anthropic key configured, SMTP configured,
  update channel writable) returned by `/health/detailed`. Each
  check shows ok/fail + a one-line explanation, so the operator
  can diagnose "what's not working" from the browser.

### Added — Production hardening
- **Sentry** integration via optional `SENTRY_DSN` env var. Empty
  = no-op. Configured with `send_default_pii=False` (Sentry never
  sees operator emails or IPs) and release tag = `app_version`.
- New `app_version` bumped to `1.2.0`.
- deploy/install.sh creates `/var/run/switchboard` on the VPS so
  the GUI's "Apply update" can drop sentinels there. Compose binds
  the same directory into the api container.
- deploy/update.sh checks the sentinel and runs immediately when
  present (otherwise it's the regular git-ahead check). Safe in
  cron — no-op when nothing's pending.

### Wired
- `GET /system/update` — current vs. latest commit + pending status
- `POST /system/update/request` — queue a deploy (202 + sentinel)
- `DELETE /system/update/request` — cancel a queued deploy
- `GET /system/certificate` — read peer cert from the public URL
- `GET /health/detailed` — per-subsystem self-test

98 backend tests pass; tsc clean.

## v1.1.0 — Self-hosted VPS deploy + update flow · 2026-05-20

### Added
- **`deploy/docker-compose.yml`** — full stack (postgres + api + web
  + Caddy reverse proxy) with named volumes for data persistence.
- **`deploy/Caddyfile`** — automatic Let's Encrypt HTTPS, no certbot
  needed; Caddy handles renewal in the background.
- **`deploy/install.sh`** — idempotent first-time install. Installs
  Docker if missing, generates `POSTGRES_PASSWORD` + `APP_SECRET_KEY`,
  prompts for domains/email, builds + starts the stack.
- **`deploy/update.sh`** — one-line update after each git push:
  fast-forwards `main`, rebuilds only changed containers, restarts
  in dependency order. No-ops when nothing changed (safe in cron).
- **`deploy/backup.sh`** — daily Postgres dump + documents-volume
  tar. Keeps last 14 days.
- **`docs/VPS_DEPLOY.md`** — non-coder walkthrough: DNS → SSH →
  install → verify → updates → backups → day-2 operations.

### Why
You can now self-host on any Ubuntu/Debian VPS in ~20 minutes
instead of using Railway. Update flow stays simple:
`git push` → `bash deploy/update.sh` on the VPS.

## v1.0.0 — Ready for first paying customer · 2026-05-20

The platform is end-to-end usable for a real US VoIP launch. The
operator can sign up, onboard a client through a magic-link portal,
let six AI agents draft regulated filings, review every gated
action in the approval queue, track each filing's lifecycle stage,
and export a compliance archive when the launch is done. Agents
learn from operator corrections via in-context lessons.

### Added — The mandatory five
- **FCC CORES (FRN)** is now a first-class application alongside
  OCN, FCC 499, RMD, and STIR/SHAKEN. Auto-derived from intake at
  entity stage. (`491dd62`)
- VoIP launch playbook (v3) and Compliance Agent prompt now spell
  out the five-filing dependency order: CORES → OCN → 499 → RMD →
  STIR/SHAKEN. (`491dd62`)

### Added — Per-filing launch tracker
- New `Application` model tracks every regulated filing per client:
  OCN, FCC 499, RMD, STIR/SHAKEN, Section 214 (if international),
  state CPCN per target state, carrier interconnects. (`1913f96`)
- Stages: not_started → in_progress → awaiting_approval → submitted
  → under_review → complete (and `blocked`). Each row carries an
  external reference number, free-text notes, and the current_agent.
- `update_application_stage` agent tool — agents self-report
  progress as they work, so the UI shows "compliance is working on
  FCC 499" in real time. (`1913f96`)
- `POST /clients/{id}/applications/sync` derives the required
  application set from intake idempotently — re-run any time
  intake changes to pick up new mandates.
- Client detail page gets a new **Launch progress** section with
  stage chips, current-agent chips, and inline stage controls.

### Added — Client onboarding playbook
- `docs/CLIENT_ONBOARDING.md` walks the operator through onboarding
  a real client phase-by-phase: kickoff → capture-once intake →
  sync launch checklist → agents draft → operator reviews → file
  on real portals → wait & track → go-live → archive. (`1913f96`)

## v0.9.0 — Production-deployable · 2026-05-20

### Added — Railway deployment
- Multi-stage Dockerfiles for `apps/api` and `apps/web`. (`5dbeffa`)
- `railway.toml` per service. API entrypoint auto-runs
  `alembic upgrade head` on every boot so a Railway deploy "just
  works" — no manual migration step.
- `docs/DEPLOY.md` — 30-minute non-coder walkthrough: GitHub →
  Railway → Postgres → API → Web → custom domain.
- Honest cleanup of `.env.example` — removed Telegram, Gmail OAuth,
  Documenso, and Sentry placeholders that were never wired.

## v0.8.0 — Six-agent roster + supervised learning · 2026-05-20

### Added — Six agents, distributed by scope (`313e39b`)
| Agent | Scope |
|---|---|
| `pm` | Orchestrator (no external action) |
| `intake` | Capture-once mandate + drafts client emails |
| `compliance` | Federal FCC: CORES, 499, RMD, Section 214 |
| `state_licensing` | Per-state CPCNs / PUC paperwork |
| `carrier` | Wholesale interconnection + OCN + STIR/SHAKEN |
| `document` | MSAs, LOAs, contract drafts, signing |

### Added — Agents learn from operator corrections
- New `AgentLesson` model + `app/agent_learning.py`. (`313e39b`)
- Every approval rejection (with reason) or edit-before-approve
  writes a lesson tagged to the agent that queued the action.
- On the next run, the agent's system prompt is prepended with
  "PAST CORRECTIONS FROM YOUR OPERATOR" — supervised in-context
  learning, no fine-tuning. Owner-scoped.
- GUI: `/agents` page shows a "learned: N" pill per agent + a
  Lessons modal with rejection / edit / manual sources. Operator
  can add manual lessons too.

## v0.7.0 — Everything goes GUI · 2026-05-20

### Added — GUI for AI Agents (`ad48db0`)
- New `/agents` page lets the operator clone & customize a built-in
  agent or compose a brand new one — pick tools from a catalog
  (with T0..T3 tier pills), write the system prompt, choose model
  and effort, enable/disable. No code changes needed.
- Tools stay code-defined for safety; tier classification is the
  load-bearing approval boundary that can't be GUI-redefined.
- `Agent` model owner-scoped; custom agent named the same as a
  built-in takes precedence at run time. Disable to fall back.

### Added — GUI for runtime config (`fdc1129`)
- Settings page can now save the **Anthropic API key**, default
  model, **SMTP** host/port/user/password/from/TLS, and the
  public app URL. No more `.env` editing.
- Secret values are encrypted at rest (Fernet); the API never
  echoes a saved secret. A leak-canary test asserts this.
- `Send test email` button verifies SMTP works before relying on it.
- All getters fall back to env when DB row is empty — existing
  `.env`-based deploys keep working.

## v0.6.0 — Public client portal + platform readiness · 2026-05-20

### Added — Public client portal (`8d4e5f1`)
- Operator generates a **magic link** from the client detail page.
- Client opens `/c/{token}` and fills intake, uploads documents,
  drops in portal credentials — all scoped to that one client.
- Every action audits as `client:{link_id}` so the operator's
  trail shows who did what.
- Credentials are write-only through the portal; clients can list
  service names already on file but never see the stored secrets.
- Links carry optional expiration, are revokable any time.

### Added — Platform readiness card (`8bf6a2e`)
- Settings page shows green/amber pills for Anthropic key, SMTP,
  public URL, portal-integration backend.
- Auth-gated; secrets are never echoed back through the API.

## v0.5.0 — Notifications + archive + filters · 2026-05-20

### Added
- **Best-effort SMTP notifications** when an agent queues an
  approval. Empty SMTP config → silent no-op; delivery failures
  never block the agent loop. (`df6ff70`)
- **Client archive zip** — one-click download of `intake.json` +
  `audit.csv` + every uploaded document. (`849db09`)
- **Approval queue filters**: by client and action_type, matching
  the History page. (`1260986`)
- **Workspaces list enrichment**: each client row now shows stage,
  intake-complete badge, open-task count, pending-approval count.
  (`1910fc7`)

## v0.4.0 — Operator UX overhaul · 2026-05-20

### Added
- **Desktop AppShell**: fixed left sidebar (brand, nav, operator
  email, sign-out) + max-w-7xl content area + live
  pending-approval badge on every page. Mobile gets a compact top
  bar. (`53a670c`, `6c90a2f`)
- **Document uploads with content-addressed storage**: multipart
  POST, SHA-256 keyed (identical bytes dedupe on disk), inline
  upload buttons next to every mandated required-doc row,
  `+ Add document` CTA, inline preview modal for image/PDF/text.
  (`09a352c`, `733a551`, `eb792b5`)
- **Document version history**: group by type, latest first, older
  versions in a collapsible sub-list. (`033a7a9`)
- **Approval notes always visible** + reject requires a non-blank
  reason at the schema level. (`cc3c7a8`)
- **Audit CSV export**: per-client and operator-wide. (`ec7af4c`)
- **Settings page**: rename profile + change password (audited;
  secret never logged). (`65fdf73`)
- **Intake wizard**: 5 labeled fieldsets replace the JSON textarea.
  (`4481f39`)

## v0.3.0 — Vault + portal actions · 2026-05-20

### Added
- **Founder-stage onboarding**: a single person can start before
  the entity exists; corporate docs unlock once EIN is captured.
  (`2807696`)
- **Encrypted credential vault**: per-client external logins (FCC,
  state PUCs, carriers). Agents see *which* services exist, never
  the secret. Every decryption audits as `credential.accessed`.
  (`c957241`)
- **Portal-action tool**: vault-gated, T2-tier. Agent picks a
  service + action; platform decrypts and runs the integration
  with the operator's approval. (`5181b1f`)
- **Per-service portal-action catalog**: registry, schema
  validation, prettier cards. (`d6a91c8`, `caa2e5d`)
- **Integration adapter + demo backend**: pluggable layer so
  Playwright / HTTP integrations slot in without touching the
  approval/audit surface. (`f9abcdd`)

## v0.2.0 — Core platform features · 2026-05-20

### Added
- **Approval queue**: review / edit&approve / reject / batch. The
  product. (`1c1a1b3`, `16988a3`)
- **PM orchestration**: workspace-aware tools + sub-task delegation
  through the same tier-gated boundary. (`671ded1`)
- **Execute on approval**: approving an action materializes a
  versioned `Document` and records the result. (`bc9452c`)
- **Bounded bulk sweep**: run all queued sub-tasks for a client,
  capped to bound API spend. (`d2d03cb`)
- **Daily ops briefing**: deterministic operator-wide digest of
  what's captured, what's blocked, what's waiting on you.
  (`c5b27a1`)
- **Auto-decided required documents**: mandate per client based on
  intake (target_states, international intent); `*required` /
  `scan` flags. (`d04152f`)
- **Immutable audit log**: every consequential action, append-only.
  (`ac120d3`)
- **Document request pack PDF**: one tailored, emailable file with
  per-document specs the client can read. (`ab7a5ac`, `3bc19dd`)
- **Action history**: filterable decided-approvals page. (`06041c7`)

## v0.1.0 — Foundation · 2026-05-19

### Added
- Monorepo skeleton (`apps/web` Next.js 14, `apps/api` FastAPI).
- Auth: register, login, JWT. (`e6b19fe`)
- Operator-scoped client workspace CRUD. (`f09f538`)
- Capture-once intake mandate. (`e043689`)
- Agent runtime (`run_agent` manual tool-use loop with tier-gated
  approval boundary). (`181f90b`)
- Three-agent roster: Compliance, Document, PM. (`18cda36`)
- E2E smoke test. (`f7125c5`)
- CORS middleware so the browser can call the API. (`82b4487`)

### Decided
- E-signature provider: **self-hosted Documenso** (not SaaS).
  Integration code is the obvious next phase. (`18303c3`)

---

## How updates flow

Switchboard auto-deploys from `main`. The flow once you've set up
Railway per [DEPLOY.md](docs/DEPLOY.md):

1. **Push to `main`** on GitHub (e.g. via this repo's commits).
2. **Railway picks it up**: rebuilds the affected service (api or
   web) and ships the new image. Watch progress in the Railway
   dashboard → service → Deployments.
3. **API container boots**, runs `alembic upgrade head` first, then
   `uvicorn`. So schema migrations apply automatically.
4. **Web container builds with the new `NEXT_PUBLIC_API_URL`** as
   a build arg and serves the new bundle.

Total: usually 2-4 minutes from push to live.

Roll back: in Railway, service → Deployments → click an earlier
green deploy → "Redeploy". Postgres migrations are *not*
auto-reverted; if a migration broke something, also run
`alembic downgrade -1` manually inside the api container.
