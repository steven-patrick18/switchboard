"use client";

import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";

type ReadinessItem = {
  application_type: string;
  application_id: string | null;
  label: string;
  owner_agent: string;
  instruction: string;
  stage: string;
  current_agent: string | null;
  blocked_on: string[] | null;
  external_ref: string | null;
  notes: string | null;
};

type Snapshot = {
  intake_complete: boolean;
  intake_missing_fields: string[];
  intake_missing_documents: string[];
  ready: ReadinessItem[];
  blocked: ReadinessItem[];
  in_flight: ReadinessItem[];
  complete: ReadinessItem[];
};

export default function NextSteps({
  clientId,
  onAgentRun,
}: {
  clientId: string;
  // Parent owns the agent-run action so it can reload tasks/audit
  // alongside the readiness snapshot.
  onAgentRun: (agent: string, instruction: string) => Promise<void>;
}) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setSnap(
        await apiFetch<Snapshot>(`/clients/${clientId}/applications/readiness`),
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load readiness");
    }
  }, [clientId]);

  useEffect(() => {
    load();
  }, [load]);

  async function start(item: ReadinessItem) {
    setBusy(item.application_type);
    setErr(null);
    try {
      await onAgentRun(item.owner_agent, item.instruction);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Start failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="mt-8">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Next steps
          </h2>
          <p className="mt-1 text-xs text-slate-500">
            Based on the intake + documents you&apos;ve captured, here&apos;s
            what we can start right now — one click kicks off the right
            agent with a pre-filled instruction.
          </p>
        </div>
        <button
          onClick={load}
          className="rounded-md border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50"
        >
          Refresh
        </button>
      </div>

      {err && (
        <p className="mt-2 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-800">
          {err}
        </p>
      )}

      {snap && !snap.intake_complete && (
        <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          <p className="font-semibold">Finish intake first.</p>
          {snap.intake_missing_fields.length > 0 && (
            <p className="mt-1 text-xs">
              Missing fields: {snap.intake_missing_fields.join(", ")}.
            </p>
          )}
          {snap.intake_missing_documents.length > 0 && (
            <p className="mt-1 text-xs">
              Missing documents (required for intake):{" "}
              {snap.intake_missing_documents.join(", ")}.
            </p>
          )}
          <p className="mt-2 text-xs">
            Once those are captured, the platform will unlock the
            corresponding next steps automatically.
          </p>
        </div>
      )}

      {snap && snap.intake_complete && (
        <div className="mt-3 space-y-4">
          {snap.ready.length > 0 && (
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-green-700">
                Ready to start ({snap.ready.length})
              </p>
              <ul className="space-y-2">
                {snap.ready.map((it) => (
                  <li
                    key={it.application_type}
                    className="rounded-md border border-green-200 bg-green-50 p-3"
                  >
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <div className="min-w-0">
                        <p className="text-sm font-semibold text-slate-900">
                          {it.label}
                        </p>
                        <p className="text-xs text-slate-600">
                          Owner:{" "}
                          <span className="font-mono">{it.owner_agent}</span>{" "}
                          · Click Start and the agent drafts the work; any
                          regulated action queues for your approval.
                        </p>
                      </div>
                      <button
                        onClick={() => start(it)}
                        disabled={busy === it.application_type}
                        className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
                      >
                        {busy === it.application_type
                          ? "Starting…"
                          : `Start ▸`}
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {snap.in_flight.length > 0 && (
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-blue-700">
                In flight ({snap.in_flight.length})
              </p>
              <ul className="space-y-2">
                {snap.in_flight.map((it) => (
                  <li
                    key={it.application_type}
                    className="rounded-md border border-blue-200 bg-blue-50 p-3 text-sm"
                  >
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <div>
                        <p className="font-semibold text-slate-900">
                          {it.label}
                        </p>
                        <p className="text-xs text-slate-600">
                          stage:{" "}
                          <span className="font-mono">{it.stage}</span>
                          {it.current_agent && (
                            <>
                              {" "}· agent:{" "}
                              <span className="font-mono">
                                {it.current_agent}
                              </span>{" "}
                              working
                            </>
                          )}
                        </p>
                        {it.notes && (
                          <p className="mt-1 text-xs text-slate-700">
                            <span className="text-slate-500">note:</span>{" "}
                            {it.notes}
                          </p>
                        )}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {snap.blocked.length > 0 && (
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-amber-700">
                Blocked ({snap.blocked.length})
              </p>
              <ul className="space-y-2">
                {snap.blocked.map((it) => (
                  <li
                    key={it.application_type}
                    className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm"
                  >
                    <p className="font-semibold text-slate-900">{it.label}</p>
                    {it.blocked_on && it.blocked_on.length > 0 && (
                      <ul className="mt-1 ml-3 list-disc text-xs text-slate-600">
                        {it.blocked_on.map((b) => (
                          <li key={b}>{b}</li>
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {snap.complete.length > 0 && (
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                Complete ({snap.complete.length})
              </p>
              <ul className="space-y-1">
                {snap.complete.map((it) => (
                  <li
                    key={it.application_type}
                    className="rounded-md border border-slate-200 bg-white p-2 text-xs text-slate-600"
                  >
                    ✓ {it.label}
                    {it.external_ref && (
                      <span className="ml-2 font-mono text-slate-500">
                        {it.external_ref}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {snap.ready.length === 0 &&
            snap.in_flight.length === 0 &&
            snap.blocked.length === 0 &&
            snap.complete.length === 0 && (
              <p className="text-sm text-slate-500">
                No applications synced yet. Open the Launch progress
                section and click <strong>Sync from intake</strong> to
                auto-derive the launch checklist.
              </p>
            )}
        </div>
      )}
    </section>
  );
}
