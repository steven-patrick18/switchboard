"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { apiFetch, downloadFile } from "@/lib/api";
import AppShell from "@/app/AppShell";

type BriefingClient = {
  client_id: string;
  name: string;
  stage: string;
  intake_complete: boolean;
  pending_approvals: number;
  open_tasks: number;
};

type Briefing = {
  generated_at: string;
  summary: string;
  totals: {
    clients: number;
    pending_approvals: number;
    agent_runs: number;
    total_cost: number;
    tasks_by_status: Record<string, number>;
  };
  attention: string[];
  clients: BriefingClient[];
};

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
      <div className="text-3xl font-semibold text-slate-900">{value}</div>
      <div className="mt-1 text-xs uppercase tracking-wide text-slate-500">
        {label}
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const [b, setB] = useState<Briefing | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setB(await apiFetch<Briefing>("/briefing"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <AppShell>
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold text-slate-900">Daily briefing</h1>
        <div className="flex items-baseline gap-4">
          {b && (
            <span className="text-xs text-slate-500">
              generated {new Date(b.generated_at).toLocaleString()}
            </span>
          )}
          <button
            onClick={() =>
              downloadFile("/audit.csv", "switchboard-audit.csv").catch((e) =>
                setError(e instanceof Error ? e.message : "Export failed"),
              )
            }
            className="rounded-md border border-slate-300 px-2 py-1 text-xs"
          >
            Export audit ↓
          </button>
        </div>
      </div>

      {error && <p className="mt-4 text-sm text-red-600">{error}</p>}
      {!b && !error && <p className="mt-4 text-sm text-slate-500">Loading...</p>}

      {b && (
        <>
          <p className="mt-3 max-w-3xl text-sm text-slate-700">{b.summary}</p>

          <div className="mt-8 grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Stat label="Clients" value={b.totals.clients} />
            <Stat label="Pending approvals" value={b.totals.pending_approvals} />
            <Stat label="Agent runs" value={b.totals.agent_runs} />
            <Stat label="Cost (USD)" value={`$${b.totals.total_cost}`} />
          </div>

          <div className="mt-10 grid grid-cols-1 gap-6 lg:grid-cols-2">
            <section>
              <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Needs your attention
              </h2>
              <ul className="mt-2 space-y-2 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
                {b.attention.map((a, i) => (
                  <li key={i} className="text-sm text-slate-700">
                    • {a}
                  </li>
                ))}
              </ul>
            </section>

            <section>
              <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Clients
              </h2>
              <ul className="mt-2 divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white shadow-sm">
                {b.clients.length === 0 && (
                  <li className="p-4 text-sm text-slate-500">
                    No clients yet.
                  </li>
                )}
                {b.clients.map((c) => (
                  <li
                    key={c.client_id}
                    className="flex items-center justify-between p-4"
                  >
                    <Link
                      href={`/clients/${c.client_id}`}
                      className="font-medium text-slate-900 hover:underline"
                    >
                      {c.name}
                    </Link>
                    <span className="text-xs text-slate-500">
                      {c.intake_complete ? "intake ✓" : "intake incomplete"} ·{" "}
                      {c.open_tasks} open · {c.pending_approvals} to approve
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        </>
      )}
    </AppShell>
  );
}
