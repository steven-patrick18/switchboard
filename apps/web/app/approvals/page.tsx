"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { apiFetch, getToken } from "@/lib/api";
import Nav from "@/app/Nav";
import ApprovalCard, {
  type Approval,
  type PortalActionSpec,
} from "./ApprovalCard";

export default function ApprovalsPage() {
  const router = useRouter();
  const [items, setItems] = useState<Approval[]>([]);
  const [catalog, setCatalog] = useState<PortalActionSpec[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
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
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    load();
  }, [load, router]);

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function batch(decision: "approved" | "rejected") {
    try {
      await apiFetch("/approvals/batch", {
        method: "POST",
        body: JSON.stringify({ ids: [...selected], decision }),
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Batch failed");
    }
  }

  return (
    <main className="mx-auto max-w-3xl p-8">
      <Nav />
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Approval queue</h1>
        <span className="text-sm text-slate-500">{items.length} pending</span>
      </div>

      {selected.size > 0 && (
        <div className="mt-4 flex items-center gap-3 rounded-md border border-slate-200 bg-slate-50 p-3">
          <span className="text-sm">{selected.size} selected</span>
          <button
            onClick={() => batch("approved")}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white"
          >
            Approve selected
          </button>
          <button
            onClick={() => batch("rejected")}
            className="rounded-md border border-red-300 px-3 py-1.5 text-sm text-red-700"
          >
            Reject selected
          </button>
        </div>
      )}

      {error && <p className="mt-4 text-sm text-red-600">{error}</p>}

      <ul className="mt-6 space-y-3">
        {loading && <li className="text-sm text-slate-500">Loading...</li>}
        {!loading && items.length === 0 && (
          <li className="rounded-lg border border-slate-200 bg-white p-6 text-center text-sm text-slate-500">
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
    </main>
  );
}
