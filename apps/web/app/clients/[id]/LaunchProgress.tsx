"use client";

import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";

export type Application = {
  id: string;
  client_id: string;
  type: string;
  label: string;
  description: string;
  default_agent: string;
  stage: string;
  current_agent: string | null;
  notes: string | null;
  external_ref: string | null;
  created_at: string;
  updated_at: string;
};

const STAGES = [
  "not_started",
  "in_progress",
  "awaiting_approval",
  "submitted",
  "under_review",
  "complete",
  "blocked",
] as const;

const STAGE_LABEL: Record<string, string> = {
  not_started: "not started",
  in_progress: "in progress",
  awaiting_approval: "awaiting approval",
  submitted: "submitted",
  under_review: "under review",
  complete: "complete",
  blocked: "blocked",
};

const STAGE_STYLE: Record<string, string> = {
  not_started: "bg-slate-100 text-slate-700",
  in_progress: "bg-blue-100 text-blue-800",
  awaiting_approval: "bg-amber-100 text-amber-800",
  submitted: "bg-indigo-100 text-indigo-800",
  under_review: "bg-purple-100 text-purple-800",
  complete: "bg-green-100 text-green-800",
  blocked: "bg-red-100 text-red-800",
};

export default function LaunchProgress({
  clientId,
}: {
  clientId: string;
}) {
  const [apps, setApps] = useState<Application[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [addType, setAddType] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setApps(await apiFetch<Application[]>(`/clients/${clientId}/applications`));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load applications");
    }
  }, [clientId]);

  useEffect(() => {
    load();
  }, [load]);

  async function sync() {
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      const r = await apiFetch<{ created: string[]; existing: string[] }>(
        `/clients/${clientId}/applications/sync`,
        { method: "POST" },
      );
      setMsg(
        r.created.length === 0
          ? "All required applications already exist."
          : `Created ${r.created.length} application(s): ${r.created.join(", ")}`,
      );
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Sync failed");
    } finally {
      setBusy(false);
    }
  }

  async function patch(id: string, body: Record<string, string | null>) {
    setBusy(true);
    setErr(null);
    try {
      await apiFetch(`/clients/${clientId}/applications/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Update failed");
    } finally {
      setBusy(false);
    }
  }

  async function remove(a: Application) {
    if (!window.confirm(`Remove "${a.label}" from this client's launch?`)) return;
    setBusy(true);
    try {
      await apiFetch(`/clients/${clientId}/applications/${a.id}`, {
        method: "DELETE",
      });
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setBusy(false);
    }
  }

  async function addManual(e: React.FormEvent) {
    e.preventDefault();
    if (!addType.trim()) return;
    setBusy(true);
    try {
      await apiFetch(`/clients/${clientId}/applications`, {
        method: "POST",
        body: JSON.stringify({ type: addType.trim() }),
      });
      setAddType("");
      setAdding(false);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Add failed");
    } finally {
      setBusy(false);
    }
  }

  const openCount = apps.filter(
    (a) => a.stage !== "complete",
  ).length;

  return (
    <section className="mt-8">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Launch progress
          </h2>
          <p className="mt-1 text-xs text-slate-500">
            Every regulated filing for this launch — OCN, FCC 499, RMD,
            STIR/SHAKEN, state CPCNs, carriers. Agents update their own
            rows; you can override any stage manually.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500">
            {openCount} open · {apps.length} total
          </span>
          <button
            onClick={sync}
            disabled={busy}
            className="rounded-md border border-slate-300 px-3 py-1 text-xs hover:bg-slate-50 disabled:opacity-50"
            title="Add any applications the intake now requires (idempotent)"
          >
            Sync from intake
          </button>
          <button
            onClick={() => setAdding((s) => !s)}
            className="rounded-md bg-slate-900 px-3 py-1 text-xs text-white hover:bg-slate-800"
          >
            + Add filing
          </button>
        </div>
      </div>

      {msg && (
        <p className="mt-2 rounded-md border border-green-200 bg-green-50 p-2 text-xs text-green-800">
          {msg}
        </p>
      )}
      {err && (
        <p className="mt-2 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-800">
          {err}
        </p>
      )}

      {adding && (
        <form
          onSubmit={addManual}
          className="mt-2 flex items-center gap-2 rounded-md border border-slate-200 bg-slate-50 p-3"
        >
          <input
            className="flex-1 rounded-md border border-slate-300 px-3 py-1.5 text-sm"
            placeholder="Type — e.g. carrier:bandwidth, state_cpcn:NY"
            value={addType}
            onChange={(e) => setAddType(e.target.value)}
            autoFocus
          />
          <button
            type="submit"
            disabled={!addType.trim() || busy}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            Add
          </button>
          <button
            type="button"
            onClick={() => {
              setAdding(false);
              setAddType("");
            }}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
          >
            Cancel
          </button>
        </form>
      )}

      <ul className="mt-3 space-y-2">
        {apps.length === 0 && (
          <li className="rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-500 shadow-sm">
            No applications yet. Hit <strong>Sync from intake</strong> to
            auto-add everything the intake mandates (OCN, FCC 499, RMD,
            STIR/SHAKEN, and a state CPCN row per target state).
          </li>
        )}
        {apps.map((a) => (
          <li
            key={a.id}
            className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold text-slate-900">
                    {a.label}
                  </span>
                  <span
                    className={
                      "rounded px-1.5 py-0.5 text-xs font-semibold " +
                      (STAGE_STYLE[a.stage] ?? "bg-slate-100 text-slate-700")
                    }
                  >
                    {STAGE_LABEL[a.stage] ?? a.stage}
                  </span>
                  {a.current_agent && (
                    <span className="rounded bg-indigo-100 px-1.5 py-0.5 text-xs font-semibold text-indigo-800">
                      {a.current_agent} working
                    </span>
                  )}
                  {!a.current_agent && a.default_agent && (
                    <span className="text-xs text-slate-400">
                      owner: {a.default_agent}
                    </span>
                  )}
                  {a.external_ref && (
                    <span className="font-mono text-xs text-slate-500">
                      {a.external_ref}
                    </span>
                  )}
                </div>
                <p className="mt-1 text-xs text-slate-500">{a.description}</p>
                {a.notes && (
                  <p className="mt-1 text-xs text-slate-700">
                    <span className="text-slate-500">note:</span> {a.notes}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-2">
                <select
                  value={a.stage}
                  onChange={(e) => patch(a.id, { stage: e.target.value })}
                  disabled={busy}
                  className="rounded-md border border-slate-300 px-2 py-1 text-xs"
                  title="Set stage manually"
                >
                  {STAGES.map((s) => (
                    <option key={s} value={s}>
                      {STAGE_LABEL[s]}
                    </option>
                  ))}
                </select>
                <button
                  onClick={() =>
                    setEditingId(editingId === a.id ? null : a.id)
                  }
                  className="rounded-md border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50"
                >
                  {editingId === a.id ? "close" : "edit"}
                </button>
                <button
                  onClick={() => remove(a)}
                  disabled={busy}
                  className="rounded-md border border-red-300 px-2 py-1 text-xs text-red-700 hover:bg-red-50 disabled:opacity-50"
                >
                  remove
                </button>
              </div>
            </div>
            {editingId === a.id && (
              <div className="mt-3 grid grid-cols-1 gap-2 border-t border-slate-200 pt-3 sm:grid-cols-[1fr_1fr_auto]">
                <input
                  defaultValue={a.external_ref ?? ""}
                  placeholder="External reference (FCC ID, NECA OCN, docket #)"
                  className="rounded-md border border-slate-300 px-2 py-1 text-xs"
                  onBlur={(e) =>
                    patch(a.id, { external_ref: e.target.value || null })
                  }
                />
                <input
                  defaultValue={a.notes ?? ""}
                  placeholder="Note (visible to agents)"
                  className="rounded-md border border-slate-300 px-2 py-1 text-xs"
                  onBlur={(e) =>
                    patch(a.id, { notes: e.target.value || null })
                  }
                />
                <button
                  onClick={() => setEditingId(null)}
                  className="rounded-md bg-slate-900 px-3 py-1 text-xs font-medium text-white"
                >
                  Done
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
