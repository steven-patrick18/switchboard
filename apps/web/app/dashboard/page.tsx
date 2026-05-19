"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { apiFetch, getToken } from "@/lib/api";
import Nav from "@/app/Nav";

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
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="text-2xl font-semibold">{value}</div>
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
    </div>
  );
}

export default function DashboardPage() {
  const router = useRouter();
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
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    load();
  }, [load, router]);

  return (
    <main className="mx-auto max-w-3xl p-8">
      <Nav />
      <h1 className="text-2xl font-semibold">Daily briefing</h1>

      {error && <p className="mt-4 text-sm text-red-600">{error}</p>}
      {!b && !error && <p className="mt-4 text-sm text-slate-500">Loading...</p>}

      {b && (
        <>
          <p className="mt-3 text-sm text-slate-700">{b.summary}</p>

          <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Clients" value={b.totals.clients} />
            <Stat label="Pending approvals" value={b.totals.pending_approvals} />
            <Stat label="Agent runs" value={b.totals.agent_runs} />
            <Stat label="Cost (USD)" value={`$${b.totals.total_cost}`} />
          </div>

          <h2 className="mt-8 text-sm font-semibold uppercase tracking-wide text-slate-500">
            Needs your attention
          </h2>
          <ul className="mt-2 space-y-1">
            {b.attention.map((a, i) => (
              <li key={i} className="text-sm text-slate-700">
                • {a}
              </li>
            ))}
          </ul>

          <h2 className="mt-8 text-sm font-semibold uppercase tracking-wide text-slate-500">
            Clients
          </h2>
          <ul className="mt-2 divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white">
            {b.clients.length === 0 && (
              <li className="p-4 text-sm text-slate-500">No clients yet.</li>
            )}
            {b.clients.map((c) => (
              <li key={c.client_id} className="flex items-center justify-between p-4">
                <Link
                  href={`/clients/${c.client_id}`}
                  className="font-medium text-slate-900 underline"
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
        </>
      )}
    </main>
  );
}
