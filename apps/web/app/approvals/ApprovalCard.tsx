"use client";

import { useState } from "react";

import { apiFetch } from "@/lib/api";

export type EmailPacket = {
  to: string;
  from_address: string;
  subject: string;
  body: string;
  cc: string[] | null;
  attachments_note: string | null;
};

export type Approval = {
  id: string;
  task_id: string;
  client_id: string;
  client_name: string;
  action_type: string;
  tier: string;
  payload: Record<string, unknown> | null;
  decision: string;
  reviewer_id: string | null;
  note: string | null;
  executed_at: string | null;
  execution_result: string | null;
  result_document_id: string | null;
  email_packet: EmailPacket | null;
  email_sent_at: string | null;
  email_message_id: string | null;
  ts: string;
};

export type PortalActionSpec = {
  service: string;
  action: string;
  label: string;
  description: string;
  required_params: string[];
  optional_params: string[];
  tier: string;
};

const TIER_STYLE: Record<string, string> = {
  T2: "bg-amber-100 text-amber-800",
  T3: "bg-red-100 text-red-800",
};

// --- TBD-placeholder scanning ---------------------------------------
// The carrier (and other filing-drafting) agents fill what they can
// read and leave '[TBD - operator fills at approval; <hint>]' for the
// few fields they genuinely cannot. Instead of forcing the operator
// to dig through JSON to find them, we recursively scan the payload
// and render a small form: one input per TBD field with the hint
// inline. On submit, the form patches the payload at the right path
// and posts an edit-and-approve.

type TbdField = {
  path: (string | number)[];
  label: string;
  hint: string;
  raw: string;
};

const TBD_RE = /^\s*\[\s*tbd\b([\s\S]*?)\]\s*$/i;

function extractTbdHint(s: string): string {
  // "[TBD - operator fills at approval; typical single OCN unless block]"
  // -> "operator fills at approval; typical single OCN unless block"
  // Also tolerates em-dash, colon, no-separator.
  const m = TBD_RE.exec(s);
  if (!m) return "";
  return m[1].replace(/^\s*[-–—:]\s*/, "").trim();
}

function humanizeKey(key: string | number): string {
  if (typeof key === "number") return `#${key + 1}`;
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/\bOcn\b/g, "OCN")
    .replace(/\bEin\b/g, "EIN")
    .replace(/\bFcc\b/g, "FCC")
    .replace(/\bFrn\b/g, "FRN")
    .replace(/\bLoa\b/g, "LOA")
    .replace(/\bRmd\b/g, "RMD")
    .replace(/\bCpcn\b/g, "CPCN");
}

function findTbdFields(
  obj: unknown,
  path: (string | number)[] = [],
): TbdField[] {
  if (typeof obj === "string" && TBD_RE.test(obj)) {
    return [
      {
        path,
        label: humanizeKey(path[path.length - 1] ?? "?"),
        hint: extractTbdHint(obj),
        raw: obj,
      },
    ];
  }
  if (Array.isArray(obj)) {
    return obj.flatMap((v, i) => findTbdFields(v, [...path, i]));
  }
  if (obj && typeof obj === "object") {
    return Object.entries(obj as Record<string, unknown>).flatMap(([k, v]) =>
      findTbdFields(v, [...path, k]),
    );
  }
  return [];
}

function setNested(obj: unknown, path: (string | number)[], value: unknown): unknown {
  if (path.length === 0) return value;
  const [head, ...rest] = path;
  if (Array.isArray(obj)) {
    const next = [...obj];
    const idx = typeof head === "number" ? head : Number(head);
    next[idx] = setNested(obj[idx], rest, value);
    return next;
  }
  const src = (obj as Record<string, unknown>) ?? {};
  return {
    ...src,
    [String(head)]: setNested(src[String(head)], rest, value),
  };
}

export default function ApprovalCard({
  approval,
  catalog,
  selected,
  onToggle,
  onChanged,
}: {
  approval: Approval;
  catalog: PortalActionSpec[];
  selected: boolean;
  onToggle: (id: string) => void;
  onChanged: () => void;
}) {
  const portalSpec =
    approval.action_type === "request_portal_action"
      ? catalog.find(
          (a) =>
            a.service === approval.payload?.service &&
            a.action === approval.payload?.action,
        ) ?? null
      : null;
  const tbdFields = findTbdFields(approval.payload ?? {});
  const summary =
    typeof approval.payload?.summary === "string"
      ? (approval.payload.summary as string)
      : "";
  const [mode, setMode] = useState<"none" | "edit" | "send_back">("none");
  const [editText, setEditText] = useState(
    JSON.stringify(approval.payload ?? {}, null, 2),
  );
  const [note, setNote] = useState("");
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Email packet UI: local copies of to/subject/body so the operator
  // can tweak before clicking Send. Seeded from approval.email_packet
  // when it exists.
  const initialPacket = approval.email_packet;
  const [emailTo, setEmailTo] = useState(initialPacket?.to ?? "");
  const [emailSubject, setEmailSubject] = useState(initialPacket?.subject ?? "");
  const [emailBody, setEmailBody] = useState(initialPacket?.body ?? "");
  const [emailStatus, setEmailStatus] = useState<{
    sent: boolean;
    message_id: string | null;
    error: string | null;
  } | null>(null);
  const [replyText, setReplyText] = useState("");
  const [replyFrom, setReplyFrom] = useState("");
  // One input per [TBD ...] placeholder found in the payload, keyed by
  // dotted path. Lets the operator answer just the missing questions
  // without scanning JSON.
  const [tbdValues, setTbdValues] = useState<Record<string, string>>({});
  const [replyResult, setReplyResult] = useState<{
    new_task_id: string;
    ran: boolean;
    new_approval_ids: string[];
    agent_reply: string | null;
  } | null>(null);
  // After send-back: surface what the agent did (or that it stayed
  // queued because no Anthropic key is configured).
  const [sendBackResult, setSendBackResult] = useState<{
    ran: boolean;
    new_task_id: string;
    new_approval_ids: string[];
    agent_reply: string | null;
  } | null>(null);

  async function call(path: string, body: unknown) {
    setBusy(true);
    setErr(null);
    try {
      await apiFetch(path, { method: "POST", body: JSON.stringify(body) });
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Action failed");
    } finally {
      setBusy(false);
    }
  }

  function approve() {
    call(`/approvals/${approval.id}/approve`, { note: note || null });
  }

  function saveEdit() {
    let parsed: unknown;
    try {
      parsed = JSON.parse(editText);
    } catch {
      setErr("Payload is not valid JSON");
      return;
    }
    call(`/approvals/${approval.id}/approve`, {
      payload_override: parsed,
      note: note || null,
    });
  }

  function reject() {
    if (!note.trim()) {
      setErr("A reason is required to reject — explain why for the audit trail.");
      return;
    }
    call(`/approvals/${approval.id}/reject`, { reason: note });
  }

  function fillTbdValuesIntoPayload(): unknown {
    let patched: unknown = approval.payload ?? {};
    for (const f of tbdFields) {
      const key = f.path.join(".");
      const v = (tbdValues[key] ?? "").trim();
      if (v) {
        patched = setNested(patched, f.path, v);
      }
    }
    return patched;
  }

  function approveWithFilledFields() {
    const missing = tbdFields.filter(
      (f) => !(tbdValues[f.path.join(".")] ?? "").trim(),
    );
    if (missing.length > 0) {
      setErr(
        "Fill in: " + missing.map((m) => m.label).join(", "),
      );
      return;
    }
    const patched = fillTbdValuesIntoPayload();
    call(`/approvals/${approval.id}/approve`, {
      payload_override: patched,
      note: note || null,
    });
  }

  async function copyText(s: string) {
    try {
      await navigator.clipboard.writeText(s);
    } catch {
      /* clipboard blocked — operator can select + copy manually */
    }
  }

  async function sendEmail() {
    setBusy(true);
    setErr(null);
    try {
      const r = await apiFetch<{
        sent: boolean;
        message_id: string | null;
        error: string | null;
      }>(`/approvals/${approval.id}/send-email`, {
        method: "POST",
        body: JSON.stringify({
          to: emailTo,
          subject: emailSubject,
          body: emailBody,
        }),
      });
      setEmailStatus(r);
      if (r.sent) onChanged();
    } catch (e) {
      setEmailStatus({
        sent: false,
        message_id: null,
        error: e instanceof Error ? e.message : "Send failed",
      });
    } finally {
      setBusy(false);
    }
  }

  async function recordReply() {
    if (!replyText.trim()) {
      setErr("Paste the reply body before submitting.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const r = await apiFetch<{
        new_task_id: string;
        ran: boolean;
        new_approval_ids: string[];
        agent_reply: string | null;
      }>(`/approvals/${approval.id}/record-reply`, {
        method: "POST",
        body: JSON.stringify({
          reply: replyText,
          from_address: replyFrom || null,
        }),
      });
      setReplyResult(r);
      setReplyText("");
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Record reply failed");
    } finally {
      setBusy(false);
    }
  }

  async function sendBack() {
    if (!feedback.trim()) {
      setErr("Tell the agent what to change — that's the whole point of send-back.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const r = await apiFetch<{
        ran: boolean;
        new_task_id: string;
        new_approval_ids: string[];
        agent_reply: string | null;
      }>(`/approvals/${approval.id}/send-back`, {
        method: "POST",
        body: JSON.stringify({ feedback }),
      });
      setSendBackResult(r);
      setMode("none");
      setFeedback("");
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Send back failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          className="mt-1"
          checked={selected}
          onChange={() => onToggle(approval.id)}
        />
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <span className="font-medium">{approval.client_name}</span>
            <span
              className={`rounded px-1.5 py-0.5 text-xs font-semibold ${
                TIER_STYLE[approval.tier] ?? "bg-slate-100 text-slate-700"
              }`}
            >
              {approval.tier}
            </span>
            <span className="text-sm text-slate-600">
              {portalSpec ? portalSpec.label : approval.action_type}
            </span>
          </div>
          {portalSpec && (
            <p className="mt-1 text-xs text-slate-500">
              {portalSpec.description}
            </p>
          )}
          {summary && (
            <p className="mt-2 rounded-md border border-slate-200 bg-white p-3 text-sm text-slate-800">
              {summary}
            </p>
          )}

          {tbdFields.length > 0 && (
            <div className="mt-3 rounded-md border border-amber-300 bg-amber-50 p-3">
              <p className="text-sm font-semibold text-amber-900">
                The agent needs {tbdFields.length} thing
                {tbdFields.length === 1 ? "" : "s"} from you:
              </p>
              <p className="mt-0.5 text-xs text-amber-800">
                Everything else is already filled in from intake. Just
                answer these and click Approve.
              </p>
              <div className="mt-3 space-y-3">
                {tbdFields.map((f) => {
                  const key = f.path.join(".");
                  return (
                    <div key={key}>
                      <label className="text-xs font-medium text-slate-700">
                        {f.label}
                      </label>
                      <input
                        value={tbdValues[key] ?? ""}
                        onChange={(e) =>
                          setTbdValues({
                            ...tbdValues,
                            [key]: e.target.value,
                          })
                        }
                        placeholder={f.hint || "Type the value here"}
                        className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
                      />
                      {f.hint && (
                        <p className="mt-1 text-xs text-slate-600">
                          ↳ {f.hint}
                        </p>
                      )}
                      <p className="mt-0.5 font-mono text-[10px] text-slate-400">
                        {key}
                      </p>
                    </div>
                  );
                })}
              </div>
              <button
                onClick={approveWithFilledFields}
                disabled={busy}
                className="mt-3 rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
              >
                Approve with these values ▸
              </button>
            </div>
          )}

          <details className="mt-3 text-xs">
            <summary className="cursor-pointer text-slate-600 underline">
              Show full filing data ({tbdFields.length > 0 ? "raw JSON " : ""}
              {Object.keys(approval.payload ?? {}).length} fields)
            </summary>
            <pre className="mt-2 overflow-x-auto rounded bg-slate-50 p-3 text-xs text-slate-700">
              {JSON.stringify(approval.payload ?? {}, null, 2)}
            </pre>
          </details>

          {/* Email packet — present once the executor has run, i.e.
              after the operator approves. Lets them send the filing
              out via SMTP or copy the prefilled fields into their
              own email client. */}
          {initialPacket && (
            <div className="mt-4 rounded-md border border-slate-200 bg-slate-50 p-3">
              <div className="flex items-baseline justify-between">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-600">
                  Email packet
                </p>
                {approval.email_sent_at && (
                  <p className="text-xs text-green-700">
                    sent {new Date(approval.email_sent_at).toLocaleString()}
                  </p>
                )}
              </div>
              <p className="mt-1 text-xs text-slate-500">
                Filings go out <strong>from the client&apos;s email</strong> —
                not Switchboard&apos;s. The platform decrypts the
                client&apos;s stored <code>client_email</code> credential
                at send time, opens their SMTP, and authenticates as the
                client. Regulators reply to whoever sent the mail, so
                the From line matters for chain-of-custody.{" "}
                <a
                  href={`/clients/${approval.client_id}`}
                  className="text-slate-700 underline"
                >
                  Add / update the credential →
                </a>
              </p>
              <div className="mt-3 grid gap-2 sm:grid-cols-[6rem_1fr]">
                <label className="self-center text-xs text-slate-600">To</label>
                <div className="flex gap-2">
                  <input
                    value={emailTo}
                    onChange={(e) => setEmailTo(e.target.value)}
                    className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
                  />
                  <button
                    onClick={() => copyText(emailTo)}
                    className="rounded-md border border-slate-300 px-2 py-1 text-xs"
                    title="Copy to clipboard"
                  >
                    copy
                  </button>
                </div>
                <label className="self-center text-xs text-slate-600">From</label>
                <div className="flex gap-2">
                  <input
                    value={initialPacket.from_address}
                    readOnly
                    className="w-full rounded-md border border-slate-300 bg-slate-100 px-2 py-1 text-sm text-slate-500"
                  />
                  <button
                    onClick={() => copyText(initialPacket.from_address)}
                    className="rounded-md border border-slate-300 px-2 py-1 text-xs"
                  >
                    copy
                  </button>
                </div>
                <label className="self-center text-xs text-slate-600">Subject</label>
                <div className="flex gap-2">
                  <input
                    value={emailSubject}
                    onChange={(e) => setEmailSubject(e.target.value)}
                    className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
                  />
                  <button
                    onClick={() => copyText(emailSubject)}
                    className="rounded-md border border-slate-300 px-2 py-1 text-xs"
                  >
                    copy
                  </button>
                </div>
                <label className="self-start pt-1 text-xs text-slate-600">Body</label>
                <div className="flex gap-2">
                  <textarea
                    value={emailBody}
                    onChange={(e) => setEmailBody(e.target.value)}
                    className="h-40 w-full rounded-md border border-slate-300 p-2 font-mono text-xs"
                  />
                  <button
                    onClick={() => copyText(emailBody)}
                    className="self-start rounded-md border border-slate-300 px-2 py-1 text-xs"
                  >
                    copy
                  </button>
                </div>
              </div>
              {initialPacket.attachments_note && (
                <p className="mt-2 text-xs text-slate-600">
                  <span className="font-semibold">Attachments:</span>{" "}
                  {initialPacket.attachments_note}
                </p>
              )}
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <button
                  onClick={sendEmail}
                  disabled={busy}
                  className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                  title="Send via configured SMTP. Fails gracefully if SMTP isn't set up — fall back to the copy buttons."
                >
                  {busy
                    ? "Sending…"
                    : approval.email_sent_at
                      ? "Send again ▸"
                      : "Send via email ▸"}
                </button>
                {emailStatus && (
                  <p
                    className={
                      "text-xs " +
                      (emailStatus.sent
                        ? "text-green-700"
                        : "text-red-700")
                    }
                  >
                    {emailStatus.sent
                      ? `Sent ✓${emailStatus.message_id ? ` (msgid ${emailStatus.message_id})` : ""}`
                      : emailStatus.error || "Send failed"}
                  </p>
                )}
              </div>

              {/* Reply received — manual paste affordance until real
                  IMAP/webhook integration ships. */}
              <div className="mt-4 border-t border-slate-200 pt-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-600">
                  Reply received?
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  Paste the inbound email body here — the {approval.action_type}
                  &apos;s original agent picks up the thread and drafts the
                  next step.
                </p>
                <input
                  value={replyFrom}
                  onChange={(e) => setReplyFrom(e.target.value)}
                  placeholder="from (optional, e.g. ocn-admin@neca.org)"
                  className="mt-2 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
                />
                <textarea
                  value={replyText}
                  onChange={(e) => setReplyText(e.target.value)}
                  placeholder="Paste the full reply body…"
                  className="mt-2 h-28 w-full rounded-md border border-slate-300 p-2 text-sm"
                />
                <div className="mt-2 flex items-center gap-2">
                  <button
                    onClick={recordReply}
                    disabled={busy}
                    className="rounded-md bg-indigo-700 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                  >
                    {busy ? "Processing…" : "Process reply with agent ▸"}
                  </button>
                  {replyResult && (
                    <p className="text-xs text-slate-700">
                      {replyResult.ran
                        ? `Agent re-ran. ${replyResult.new_approval_ids.length} new approval(s) queued.`
                        : "Reply recorded — new task queued for the agent."}
                    </p>
                  )}
                </div>
              </div>
            </div>
          )}

          {mode === "edit" && (
            <textarea
              className="mt-3 h-40 w-full rounded-md border border-slate-300 p-2 font-mono text-xs"
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
            />
          )}

          {mode === "send_back" && (
            <div className="mt-3 rounded-md border border-indigo-200 bg-indigo-50 p-3">
              <label className="text-xs font-semibold uppercase tracking-wide text-indigo-900">
                What needs to change?
              </label>
              <p className="mt-1 text-xs text-indigo-800">
                The agent will be re-run with this feedback prepended to
                the original task. The current approval is rejected
                (captured as a lesson). You&apos;ll get a fresh draft to
                approve.
              </p>
              <textarea
                className="mt-2 h-28 w-full rounded-md border border-indigo-300 bg-white p-2 text-sm"
                placeholder="e.g. FRN should be 0001234567 — fill it in instead of TBD."
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
              />
            </div>
          )}

          {mode !== "send_back" && (
            <input
              className="mt-3 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
              placeholder="Note (optional for approve / edit; required for reject)"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          )}

          {err && <p className="mt-2 text-sm text-red-600">{err}</p>}

          {sendBackResult && (
            <div className="mt-3 rounded-md border border-green-200 bg-green-50 p-3 text-xs text-slate-800">
              <p className="font-semibold text-green-900">
                {sendBackResult.ran
                  ? `Agent re-ran. ${sendBackResult.new_approval_ids.length} new approval(s) queued.`
                  : "Sent back — new task queued. Click Start ▸ on the client page to run it (or set the Anthropic key in Settings)."}
              </p>
              {sendBackResult.agent_reply && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-slate-600 underline">
                    Show agent reply
                  </summary>
                  <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-white p-2">
                    {sendBackResult.agent_reply}
                  </pre>
                </details>
              )}
              <button
                onClick={() => setSendBackResult(null)}
                className="mt-2 text-xs text-slate-500 underline"
              >
                dismiss
              </button>
            </div>
          )}

          <div className="mt-3 flex flex-wrap gap-2">
            {mode === "none" && (
              <>
                <button
                  disabled={busy}
                  onClick={approve}
                  className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
                >
                  Approve
                </button>
                <button
                  disabled={busy}
                  onClick={() => setMode("edit")}
                  className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
                >
                  Edit &amp; approve
                </button>
                <button
                  disabled={busy}
                  onClick={() => setMode("send_back")}
                  className="rounded-md border border-indigo-300 px-3 py-1.5 text-sm text-indigo-700 hover:bg-indigo-50"
                  title="Reject this draft and ask the agent to redo it with your feedback"
                >
                  Send back to agent
                </button>
                <button
                  disabled={busy}
                  onClick={reject}
                  className="rounded-md border border-red-300 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50"
                >
                  Reject
                </button>
              </>
            )}
            {mode === "edit" && (
              <>
                <button
                  disabled={busy}
                  onClick={saveEdit}
                  className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
                >
                  Save &amp; approve
                </button>
                <button
                  disabled={busy}
                  onClick={() => setMode("none")}
                  className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
                >
                  Cancel edit
                </button>
              </>
            )}
            {mode === "send_back" && (
              <>
                <button
                  disabled={busy}
                  onClick={sendBack}
                  className="rounded-md bg-indigo-700 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
                >
                  {busy ? "Sending…" : "Send back ▸"}
                </button>
                <button
                  disabled={busy}
                  onClick={() => {
                    setMode("none");
                    setFeedback("");
                  }}
                  className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
                >
                  Cancel
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </li>
  );
}
