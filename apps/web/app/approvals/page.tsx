"use client";

import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import AppShell from "@/app/AppShell";
import ApprovalCard, {
  type Approval,
  type PortalActionSpec,
} from "./ApprovalCard";

export default function ApprovalsPage() {
  const [items, setItems] = useState<Approval[]>([]);
  const [catalog, setCatalog] = useState<PortalActionSpec[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchNote, setBatchNote] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [list, cat] = await Promise.all([
        apiFetch<Approval[]>("/approvals"),
        apiFetch<PortalActionSpec[]>("/portal-actions"),
      ]);
      setItems(list);
      setCatalog(cat);
      setSelected(new Set());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function batch(decision: "approved" | "rejected") {
    if (decision === "rejected" && !batchNote.trim()) {
      setError("A note is required when rejecting a batch — explain why for the audit trail.");
      return;
    }
    try {
      setError(null);
      await apiFetch("/approvals/batch", {
        method: "POST",
        body: JSON.stringify({
          ids: [...selected],
          decision,
          note: batchNote || null,
        }),
      });
      setBatchNote("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Batch failed");
    }
  }

  return (
    <AppShell>
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold text-slate-900">
          Approval queue
        </h1>
        <span className="text-sm text-slate-500">{items.length} pending</span>
      </div>

      {selected.size > 0 && (
        <div className="sticky top-0 z-10 mt-4 flex flex-wrap items-center gap-3 rounded-md border border-slate-200 bg-white p-3 shadow-sm">
          <span className="text-sm font-medium">{selected.size} selected</span>
          <input
            className="min-w-0 flex-1 rounded-md border border-slate-300 px-3 py-1.5 text-sm"
            placeholder="Batch note (optional for approve; required for reject)"
            value={batchNote}
            onChange={(e) => setBatchNote(e.target.value)}
          />
          <button
            onClick={() => batch("approved")}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800"
          >
            Approve selected
          </button>
          <button
            onClick={() => batch("rejected")}
            className="rounded-md border border-red-300 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50"
          >
            Reject selected
          </button>
        </div>
      )}

      {error && <p className="mt-4 text-sm text-red-600">{error}</p>}

      <ul className="mt-6 space-y-3">
        {loading && <li className="text-sm text-slate-500">Loading...</li>}
        {!loading && items.length === 0 && (
          <li className="rounded-lg border border-slate-200 bg-white p-10 text-center text-sm text-slate-500 shadow-sm">
            Queue is clear. Nothing waiting on you.
          </li>
        )}
        {items.map((a) => (
          <ApprovalCard
            key={a.id}
            approval={a}
            catalog={catalog}
            selected={selected.has(a.id)}
            onToggle={toggle}
            onChanged={load}
          />
        ))}
      </ul>
    </AppShell>
  );
}
