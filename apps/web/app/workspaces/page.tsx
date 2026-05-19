"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import AppShell from "@/app/AppShell";

type Client = {
  id: string;
  name: string;
  state: string | null;
  stage: string;
  created_at: string;
};

export default function WorkspacesPage() {
  const [clients, setClients] = useState<Client[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    try {
      setClients(await apiFetch<Client[]>("/clients"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

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
          <li className="p-4 text-sm text-slate-500">No clients yet.</li>
        )}
        {clients.map((c) => (
          <li
            key={c.id}
            className="flex items-center justify-between p-4 hover:bg-slate-50"
          >
            <Link
              href={`/clients/${c.id}`}
              className="font-medium text-slate-900 hover:underline"
            >
              {c.name}
            </Link>
            <span className="text-xs uppercase tracking-wide text-slate-500">
              {c.stage}
              {c.state ? ` · ${c.state}` : ""}
            </span>
          </li>
        ))}
      </ul>
    </AppShell>
  );
}
