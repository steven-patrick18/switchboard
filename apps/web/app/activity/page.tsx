"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import AppShell from "../AppShell";
import { apiFetch } from "@/lib/api";

type TaskRow = {
  task_id: string;
  agent: string;
  client_id: string | null;
  client_name: string;
  instruction: string;
  status: string;
  started_at: string | null;
};

type AssignmentRow = {
  client_id: string;
  client_name: string;
  application_type: string;
  application_label: string;
  stage: string;
  current_agent: string;
  notes: string | null;
  updated_at: string | null;
};

type RunRow = {
  run_id: string;
  agent: string;
  task_id: string | null;
  task_status: string | null;
  client_id: string | null;
  client_name: string;
  instruction: string;
  started_at: string | null;
  duration_ms: number;
  tokens_in: number;
  tokens_out: number;
  cost: number;
};

type Snap = {
  generated_at: string;
  running_now: TaskRow[];
  awaiting_approval: TaskRow[];
  current_assignments: AssignmentRow[];
  recent_runs: RunRow[];
  totals: {
    running: number;
    awaiting_approval: number;
    assignments_active: number;
    runs_last_24h: number;
    tokens_last_24h: number;
    cost_last_24h: number;
  };
};

const POLL_MS = 5000;

function relTime(iso: string | null): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const dt = Math.max(0, Date.now() - t);
  if (dt < 5_000) return "just now";
  if (dt < 60_000) return `${Math.floor(dt / 1000)}s ago`;
  if (dt < 3_600_000) return `${Math.floor(dt / 60_000)}m ago`;
  if (dt < 86_400_000) return `${Math.floor(dt / 3_600_000)}h ago`;
  return new Date(iso).toLocaleString();
}

function fmtDuration(ms: number): string {
  if (!ms) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60_000).toFixed(1)}m`;
}

export default function ActivityPage() {
  const [snap, setSnap] = useState<Snap | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const [lastTick, setLastTick] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const s = await apiFetch<Snap>("/activity");
      setSnap(s);
      setErr(null);
      setLastTick(Date.now());
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load activity");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (paused) return;
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [paused, load]);

  return (
    <AppShell>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Live activity</h1>
          <p className="mt-1 text-sm text-slate-500">
            Everything every agent is doing across all your clients —
            updates every {POLL_MS / 1000}s.
          </p>
        </div>
        <div className="flex items-center gap-3 text-xs text-slate-500">
          <span>
            {paused
              ? "paused"
              : lastTick
                ? `last refreshed ${relTime(new Date(lastTick).toISOString())}`
                : "loading…"}
          </span>
          <button
            onClick={() => setPaused((p) => !p)}
            className="rounded-md border border-slate-300 px-2 py-1 hover:bg-slate-50"
          >
            {paused ? "Resume" : "Pause"}
          </button>
          <button
            onClick={load}
            className="rounded-md border border-slate-300 px-2 py-1 hover:bg-slate-50"
          >
            Refresh now
          </button>
        </div>
      </div>

      {err && (
        <div className="mt-3 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {err}
        </div>
      )}

      {snap && (
        <>
          {/* Headline tiles */}
          <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {[
              { label: "Running", value: snap.totals.running, accent: "text-green-700" },
              {
                label: "Awaiting approval",
                value: snap.totals.awaiting_approval,
                accent: "text-amber-700",
              },
              {
                label: "Assignments active",
                value: snap.totals.assignments_active,
                accent: "text-blue-700",
              },
              {
                label: "Runs (24h)",
                value: snap.totals.runs_last_24h,
                accent: "text-slate-700",
              },
              {
                label: "Tokens (24h)",
                value: snap.totals.tokens_last_24h.toLocaleString(),
                accent: "text-slate-700",
              },
              {
                label: "Cost (24h)",
                value: `$${snap.totals.cost_last_24h.toFixed(2)}`,
                accent: "text-slate-700",
              },
            ].map((t) => (
              <div
                key={t.label}
                className="rounded-md border border-slate-200 bg-white p-3"
              >
                <p className="text-xs uppercase tracking-wide text-slate-500">
                  {t.label}
                </p>
                <p className={`mt-1 text-xl font-semibold ${t.accent}`}>
                  {t.value}
                </p>
              </div>
            ))}
          </div>

          {/* Running now */}
          <section className="mt-8">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-green-700">
              Running now ({snap.running_now.length})
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              An agent loop is actively executing — tools are firing,
              the model is thinking.
            </p>
            {snap.running_now.length === 0 ? (
              <p className="mt-2 text-sm text-slate-500">
                No agent is mid-run right now.
              </p>
            ) : (
              <ul className="mt-2 space-y-2">
                {snap.running_now.map((t) => (
                  <li
                    key={t.task_id}
                    className="rounded-md border border-green-200 bg-green-50 p-3"
                  >
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <p className="text-sm">
                        <span className="font-mono font-semibold">
                          {t.agent}
                        </span>{" "}
                        on{" "}
                        {t.client_id ? (
                          <Link
                            href={`/clients/${t.client_id}`}
                            className="font-medium underline"
                          >
                            {t.client_name}
                          </Link>
                        ) : (
                          <span className="font-medium">{t.client_name}</span>
                        )}
                      </p>
                      <p className="text-xs text-slate-500">
                        started {relTime(t.started_at)}
                      </p>
                    </div>
                    {t.instruction && (
                      <p className="mt-1 text-xs text-slate-700">
                        “{t.instruction}”
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* Awaiting approval */}
          <section className="mt-8">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-amber-700">
              Awaiting your approval ({snap.awaiting_approval.length})
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              The agent drafted something tier-2/3 and is paused until
              you decide.
            </p>
            {snap.awaiting_approval.length === 0 ? (
              <p className="mt-2 text-sm text-slate-500">Nothing queued.</p>
            ) : (
              <ul className="mt-2 space-y-2">
                {snap.awaiting_approval.map((t) => (
                  <li
                    key={t.task_id}
                    className="rounded-md border border-amber-200 bg-amber-50 p-3"
                  >
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <p className="text-sm">
                        <span className="font-mono font-semibold">
                          {t.agent}
                        </span>{" "}
                        on{" "}
                        {t.client_id ? (
                          <Link
                            href={`/clients/${t.client_id}`}
                            className="font-medium underline"
                          >
                            {t.client_name}
                          </Link>
                        ) : (
                          <span className="font-medium">{t.client_name}</span>
                        )}
                      </p>
                      <Link
                        href="/approvals"
                        className="text-xs font-medium text-slate-700 underline"
                      >
                        Go to queue →
                      </Link>
                    </div>
                    {t.instruction && (
                      <p className="mt-1 text-xs text-slate-700">
                        “{t.instruction}”
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* Current assignments */}
          <section className="mt-8">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-blue-700">
              Who's working on what ({snap.current_assignments.length})
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              Application rows an agent has explicitly claimed via
              `update_application_stage`. Stage + agent come straight
              from the launch tracker.
            </p>
            {snap.current_assignments.length === 0 ? (
              <p className="mt-2 text-sm text-slate-500">
                No filings actively claimed right now.
              </p>
            ) : (
              <ul className="mt-2 space-y-2">
                {snap.current_assignments.map((a) => (
                  <li
                    key={`${a.client_id}-${a.application_type}`}
                    className="rounded-md border border-blue-200 bg-blue-50 p-3"
                  >
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <p className="text-sm">
                        <Link
                          href={`/clients/${a.client_id}`}
                          className="font-medium underline"
                        >
                          {a.client_name}
                        </Link>{" "}
                        ·{" "}
                        <span className="font-semibold">
                          {a.application_label}
                        </span>{" "}
                        ·{" "}
                        <span className="font-mono text-xs">
                          {a.current_agent}
                        </span>{" "}
                        is{" "}
                        <span className="font-mono text-xs">{a.stage}</span>
                      </p>
                      <p className="text-xs text-slate-500">
                        updated {relTime(a.updated_at)}
                      </p>
                    </div>
                    {a.notes && (
                      <p className="mt-1 text-xs text-slate-700">
                        <span className="text-slate-500">note:</span>{" "}
                        {a.notes}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* Recent runs */}
          <section className="mt-8 mb-4">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-600">
              Recent agent runs ({snap.recent_runs.length})
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              Last 30 completed loops, newest first. Each row shows
              tokens in/out and the spend.
            </p>
            {snap.recent_runs.length === 0 ? (
              <p className="mt-2 text-sm text-slate-500">
                No agent runs yet. Click <strong>Start ▸</strong> on a
                client&apos;s Next-steps to fire one.
              </p>
            ) : (
              <div className="mt-2 overflow-x-auto rounded-md border border-slate-200 bg-white">
                <table className="min-w-full text-xs">
                  <thead className="bg-slate-50 text-slate-600">
                    <tr>
                      <th className="px-3 py-2 text-left font-medium">When</th>
                      <th className="px-3 py-2 text-left font-medium">Agent</th>
                      <th className="px-3 py-2 text-left font-medium">Client</th>
                      <th className="px-3 py-2 text-left font-medium">
                        Instruction
                      </th>
                      <th className="px-3 py-2 text-right font-medium">
                        Duration
                      </th>
                      <th className="px-3 py-2 text-right font-medium">
                        Tokens
                      </th>
                      <th className="px-3 py-2 text-right font-medium">Cost</th>
                      <th className="px-3 py-2 text-left font-medium">Task</th>
                    </tr>
                  </thead>
                  <tbody>
                    {snap.recent_runs.map((r) => (
                      <tr
                        key={r.run_id}
                        className="border-t border-slate-100"
                      >
                        <td className="px-3 py-2 text-slate-500">
                          {relTime(r.started_at)}
                        </td>
                        <td className="px-3 py-2 font-mono">{r.agent}</td>
                        <td className="px-3 py-2">
                          {r.client_id ? (
                            <Link
                              href={`/clients/${r.client_id}`}
                              className="underline"
                            >
                              {r.client_name}
                            </Link>
                          ) : (
                            r.client_name
                          )}
                        </td>
                        <td className="px-3 py-2 text-slate-700">
                          {r.instruction || "—"}
                        </td>
                        <td className="px-3 py-2 text-right text-slate-600">
                          {fmtDuration(r.duration_ms)}
                        </td>
                        <td className="px-3 py-2 text-right text-slate-600">
                          {r.tokens_in.toLocaleString()} /{" "}
                          {r.tokens_out.toLocaleString()}
                        </td>
                        <td className="px-3 py-2 text-right text-slate-600">
                          ${r.cost.toFixed(4)}
                        </td>
                        <td className="px-3 py-2">
                          {r.task_status ? (
                            <span className="font-mono text-xs text-slate-500">
                              {r.task_status}
                            </span>
                          ) : (
                            <span className="text-slate-400">—</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </AppShell>
  );
}
