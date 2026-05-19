"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
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
  clients: BriefingClient[];
};

export default function WorkspacesPage() {
  const [clients, setClients] = useState<BriefingClient[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const b = await apiFetch<Briefing>("/briefing");
      setClients(b.clients);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function createClient(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    try {
      await apiFetch("/clients", {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      setName("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create");
    }
  }

  return (
    <AppShell>
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold text-slate-900">
          Client workspaces
        </h1>
        <span className="text-xs text-slate-500">
          {clients.length} client{clients.length === 1 ? "" : "s"}
        </span>
      </div>

      <form onSubmit={createClient} className="mt-6 flex max-w-2xl gap-2">
        <input
          className="flex-1 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
          placeholder="New client name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <button
          type="submit"
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
        >
          Add client
        </button>
      </form>

      {error && <p className="mt-4 text-sm text-red-600">{error}</p>}

      <ul className="mt-6 divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white shadow-sm">
        {loading && <li className="p-4 text-sm text-slate-500">Loading...</li>}
        {!loading && clients.length === 0 && (
          <li className="p-4 text-sm text-slate-500">
            No clients yet — add the first one above.
          </li>
        )}
        {clients.map((c) => (
          <li
            key={c.client_id}
            className="flex items-center justify-between gap-3 p-4 hover:bg-slate-50"
          >
            <div className="min-w-0">
              <Link
                href={`/clients/${c.client_id}`}
                className="font-medium text-slate-900 hover:underline"
              >
                {c.name}
              </Link>
              <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                <span className="uppercase tracking-wide">{c.stage}</span>
                <span>·</span>
                <span
                  className={
                    c.intake_complete
                      ? "rounded bg-green-100 px-1.5 py-0.5 font-semibold text-green-800"
                      : "rounded bg-amber-100 px-1.5 py-0.5 font-semibold text-amber-800"
                  }
                >
                  {c.intake_complete ? "intake ✓" : "intake incomplete"}
                </span>
                {c.open_tasks > 0 && (
                  <span>
                    {c.open_tasks} open task{c.open_tasks === 1 ? "" : "s"}
                  </span>
                )}
                {c.pending_approvals > 0 && (
                  <span className="rounded bg-amber-100 px-1.5 py-0.5 font-semibold text-amber-800">
                    {c.pending_approvals} to approve
                  </span>
                )}
              </div>
            </div>
            <Link
              href={`/clients/${c.client_id}`}
              className="text-xs text-slate-500 hover:text-slate-900 hover:underline"
            >
              open →
            </Link>
          </li>
        ))}
      </ul>
    </AppShell>
  );
}
