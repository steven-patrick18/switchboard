"use client";

import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import AppShell from "@/app/AppShell";
import ApprovalCard, {
  type Approval,
  type PortalActionSpec,
} from "./ApprovalCard";

type ClientLite = { id: string; name: string };

export default function ApprovalsPage() {
  const [items, setItems] = useState<Approval[]>([]);
  const [catalog, setCatalog] = useState<PortalActionSpec[]>([]);
  const [clients, setClients] = useState<ClientLite[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchNote, setBatchNote] = useState("");
  const [filterClient, setFilterClient] = useState("");
  const [filterAction, setFilterAction] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const qs = new URLSearchParams();
      if (filterClient) qs.set("client_id", filterClient);
      if (filterAction.trim()) qs.set("action_type", filterAction.trim());
      const url = qs.toString() ? `/approvals?${qs}` : "/approvals";
      const list = await apiFetch<Approval[]>(url);
      setItems(list);
      setSelected(new Set());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [filterClient, filterAction]);

  // Sidebar data (clients + portal-action catalog) loads once; queue
  // reloads whenever a filter changes.
  useEffect(() => {
    (async () => {
      try {
        const [cs, cat] = await Promise.all([
          apiFetch<ClientLite[]>("/clients"),
          apiFetch<PortalActionSpec[]>("/portal-actions"),
        ]);
        setClients(cs);
        setCatalog(cat);
      } catch {
        // load() surfaces the primary error.
      }
    })();
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function batch(decision: "approved" | "rejected") {
    if (decision === "rejected" && !batchNote.trim()) {
      setError(
        "A note is required when rejecting a batch — explain why for the audit trail.",
      );
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

  const hasFilters = filterClient !== "" || filterAction.trim() !== "";

  return (
    <AppShell>
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold text-slate-900">
          Approval queue
        </h1>
        <span className="text-sm text-slate-500">{items.length} pending</span>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-[1fr_1fr_auto]">
        <select
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
          value={filterClient}
          onChange={(e) => setFilterClient(e.target.value)}
        >
          <option value="">All clients</option>
          {clients.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <input
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
          placeholder="action_type (optional)"
          value={filterAction}
          onChange={(e) => setFilterAction(e.target.value)}
        />
        <button
          onClick={() => {
            setFilterClient("");
            setFilterAction("");
          }}
          disabled={!hasFilters}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-50"
        >
          Clear filters
        </button>
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
            {hasFilters
              ? "No pending approvals match those filters."
              : "Queue is clear. Nothing waiting on you."}
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
