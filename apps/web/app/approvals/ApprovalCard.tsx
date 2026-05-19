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
  ts: string;
};

const TIER_STYLE: Record<string, string> = {
  T2: "bg-amber-100 text-amber-800",
  T3: "bg-red-100 text-red-800",
};

export default function ApprovalCard({
  approval,
  selected,
  onToggle,
  onChanged,
}: {
  approval: Approval;
  selected: boolean;
  onToggle: (id: string) => void;
  onChanged: () => void;
}) {
  const [mode, setMode] = useState<"none" | "edit" | "reject">("none");
  const [editText, setEditText] = useState(
    JSON.stringify(approval.payload ?? {}, null, 2),
  );
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

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
            <span className="text-sm text-slate-600">{approval.action_type}</span>
          </div>
          <pre className="mt-2 overflow-x-auto rounded bg-slate-50 p-3 text-xs text-slate-700">
            {JSON.stringify(approval.payload ?? {}, null, 2)}
          </pre>

          {mode === "edit" && (
            <div className="mt-3 space-y-2">
              <textarea
                className="h-40 w-full rounded-md border border-slate-300 p-2 font-mono text-xs"
                value={editText}
                onChange={(e) => setEditText(e.target.value)}
              />
              <input
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                placeholder="Note (optional)"
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
            </div>
          )}

          {mode === "reject" && (
            <input
              className="mt-3 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
              placeholder="Reason for rejection"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          )}

          {err && <p className="mt-2 text-sm text-red-600">{err}</p>}

          <div className="mt-3 flex flex-wrap gap-2">
            {mode === "none" && (
              <>
                <button
                  disabled={busy}
                  onClick={() => call(`/approvals/${approval.id}/approve`, {})}
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
                  onClick={() => setMode("reject")}
                  className="rounded-md border border-red-300 px-3 py-1.5 text-sm text-red-700"
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
                  Cancel
                </button>
              </>
            )}
            {mode === "reject" && (
              <>
                <button
                  disabled={busy}
                  onClick={() =>
                    call(`/approvals/${approval.id}/reject`, {
                      reason: reason || null,
                    })
                  }
                  className="rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
                >
                  Confirm reject
                </button>
                <button
                  disabled={busy}
                  onClick={() => setMode("none")}
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
