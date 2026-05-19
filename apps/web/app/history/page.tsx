"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { apiFetch, getToken } from "@/lib/api";
import Nav from "@/app/Nav";
import { type Approval, type PortalActionSpec } from "@/app/approvals/ApprovalCard";

type ClientLite = { id: string; name: string };

const DECISION_OPTIONS = [
  { value: "decided", label: "All decided" },
  { value: "approved", label: "Approved" },
  { value: "edited", label: "Edited" },
  { value: "rejected", label: "Rejected" },
];

const DECISION_STYLE: Record<string, string> = {
  approved: "bg-green-100 text-green-800",
  edited: "bg-emerald-100 text-emerald-800",
  rejected: "bg-red-100 text-red-800",
};

const TIER_STYLE: Record<string, string> = {
  T2: "bg-amber-100 text-amber-800",
  T3: "bg-red-100 text-red-800",
};

export default function HistoryPage() {
  const router = useRouter();
  const [items, setItems] = useState<Approval[]>([]);
  const [clients, setClients] = useState<ClientLite[]>([]);
  const [catalog, setCatalog] = useState<PortalActionSpec[]>([]);
  const [decision, setDecision] = useState("decided");
  const [clientId, setClientId] = useState("");
  const [actionType, setActionType] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setError(null);
    try {
      const qs = new URLSearchParams({ decision });
      if (clientId) qs.set("client_id", clientId);
      if (actionType.trim()) qs.set("action_type", actionType.trim());
      const list = await apiFetch<Approval[]>(`/approvals?${qs}`);
      setItems(list);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [decision, clientId, actionType]);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    (async () => {
      try {
        const [c, cat] = await Promise.all([
          apiFetch<ClientLite[]>("/clients"),
          apiFetch<PortalActionSpec[]>("/portal-actions"),
        ]);
        setClients(c);
        setCatalog(cat);
      } catch {
        // ignore — load() shows the primary error
      }
    })();
    load();
  }, [load, router]);

  function specFor(a: Approval): PortalActionSpec | null {
    if (a.action_type !== "request_portal_action") return null;
    return (
      catalog.find(
        (s) =>
          s.service === a.payload?.service && s.action === a.payload?.action,
      ) ?? null
    );
  }

  return (
    <main className="mx-auto max-w-3xl p-8">
      <Nav />
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">History</h1>
        <span className="text-sm text-slate-500">{items.length} item(s)</span>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-3">
        <select
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
          value={decision}
          onChange={(e) => setDecision(e.target.value)}
        >
          {DECISION_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <select
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
          value={clientId}
          onChange={(e) => setClientId(e.target.value)}
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
          value={actionType}
          onChange={(e) => setActionType(e.target.value)}
        />
      </div>

      {error && <p className="mt-4 text-sm text-red-600">{error}</p>}

      <ul className="mt-6 space-y-3">
        {loading && <li className="text-sm text-slate-500">Loading...</li>}
        {!loading && items.length === 0 && (
          <li className="rounded-lg border border-slate-200 bg-white p-6 text-center text-sm text-slate-500">
            No history matches these filters.
          </li>
        )}
        {items.map((a) => {
          const spec = specFor(a);
          return (
            <li key={a.id} className="rounded-lg border border-slate-200 bg-white p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{a.client_name}</span>
                <span
                  className={`rounded px-1.5 py-0.5 text-xs font-semibold ${
                    TIER_STYLE[a.tier] ?? "bg-slate-100 text-slate-700"
                  }`}
                >
                  {a.tier}
                </span>
                <span
                  className={`rounded px-1.5 py-0.5 text-xs font-semibold ${
                    DECISION_STYLE[a.decision] ?? "bg-slate-100 text-slate-700"
                  }`}
                >
                  {a.decision}
                </span>
                <span className="text-sm text-slate-600">
                  {spec ? spec.label : a.action_type}
                </span>
                <span className="ml-auto whitespace-nowrap text-xs text-slate-400">
                  {new Date(a.ts).toLocaleString()}
                </span>
              </div>
              {spec && (
                <p className="mt-1 text-xs text-slate-500">{spec.description}</p>
              )}
              {a.note && (
                <p className="mt-2 text-sm text-slate-700">
                  <span className="text-xs uppercase tracking-wide text-slate-500">
                    note:{" "}
                  </span>
                  {a.note}
                </p>
              )}
              {a.execution_result && (
                <pre className="mt-2 overflow-x-auto rounded bg-slate-50 p-3 text-xs text-slate-700">
                  {a.execution_result}
                </pre>
              )}
            </li>
          );
        })}
      </ul>
    </main>
  );
}
