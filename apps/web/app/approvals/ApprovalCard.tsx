"use client";

import { useState } from "react";

import { apiFetch } from "@/lib/api";

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
  const [mode, setMode] = useState<"none" | "edit" | "send_back">("none");
  const [editText, setEditText] = useState(
    JSON.stringify(approval.payload ?? {}, null, 2),
  );
  const [note, setNote] = useState("");
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
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
          <pre className="mt-2 overflow-x-auto rounded bg-slate-50 p-3 text-xs text-slate-700">
            {JSON.stringify(approval.payload ?? {}, null, 2)}
          </pre>

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
