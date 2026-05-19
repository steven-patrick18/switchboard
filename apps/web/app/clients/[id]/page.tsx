"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { apiFetch, downloadFile, getToken } from "@/lib/api";
import Nav from "@/app/Nav";

type RequiredDoc = {
  key: string;
  label: string;
  mandatory: boolean;
  needs_scan: boolean;
  provided: boolean;
};
type Completeness = {
  complete: boolean;
  stage: string;
  missing_fields: string[];
  missing_documents: string[];
  required_documents: RequiredDoc[];
};
type IntakeStatus = {
  intake: Record<string, unknown> | null;
  completeness: Completeness;
};
type Doc = { id: string; type: string; version: number; created_at: string };
type Task = {
  id: string;
  agent: string;
  status: string;
  input: Record<string, unknown> | null;
};
type ClientMeta = { id: string; name: string; stage: string };
type Audit = {
  id: string;
  ts: string;
  actor: string;
  action: string;
  subject: string;
};

const AGENTS = ["pm", "compliance", "document"];

export default function ClientDetailPage() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();

  const [meta, setMeta] = useState<ClientMeta | null>(null);
  const [intake, setIntake] = useState<IntakeStatus | null>(null);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [audit, setAudit] = useState<Audit[]>([]);
  const [intakeText, setIntakeText] = useState("{}");
  const [docType, setDocType] = useState("");
  const [agent, setAgent] = useState("compliance");
  const [instruction, setInstruction] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [clients, st, d, t, au] = await Promise.all([
        apiFetch<ClientMeta[]>("/clients"),
        apiFetch<IntakeStatus>(`/clients/${id}/intake`),
        apiFetch<Doc[]>(`/clients/${id}/documents`),
        apiFetch<Task[]>(`/clients/${id}/tasks`),
        apiFetch<Audit[]>(`/clients/${id}/audit`),
      ]);
      setMeta(clients.find((c) => c.id === id) ?? null);
      setIntake(st);
      setIntakeText(JSON.stringify(st.intake ?? {}, null, 2));
      setDocs(d);
      setTasks(t);
      setAudit(au);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Failed to load");
    }
  }, [id]);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    load();
  }, [load, router]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      setMsg(ok);
      await load();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Action failed");
    } finally {
      setBusy(false);
    }
  }

  function saveIntake() {
    let parsed: unknown;
    try {
      parsed = JSON.parse(intakeText);
    } catch {
      setMsg("Intake is not valid JSON");
      return;
    }
    act(
      () =>
        apiFetch(`/clients/${id}/intake`, {
          method: "PUT",
          body: JSON.stringify(parsed),
        }),
      "Intake saved.",
    );
  }

  async function runAgent() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await apiFetch<{ text: string; approval_ids: string[] }>(
        `/agents/${agent}/run`,
        {
          method: "POST",
          body: JSON.stringify({ client_id: id, instruction }),
        },
      );
      setMsg(
        `${agent} ran. ${r.approval_ids.length} approval(s) queued. ` +
          `Response: ${r.text.slice(0, 240)}`,
      );
      await load();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Run failed");
    } finally {
      setBusy(false);
    }
  }

  const c = intake?.completeness;

  return (
    <main className="mx-auto max-w-3xl p-8">
      <Nav />
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">{meta?.name ?? "Client"}</h1>
        <Link href="/approvals" className="text-sm text-slate-500 underline">
          Approval queue →
        </Link>
      </div>
      {meta && (
        <p className="mt-1 text-xs uppercase tracking-wide text-slate-500">
          stage: {meta.stage}
        </p>
      )}

      {msg && (
        <p className="mt-4 rounded-md bg-slate-100 p-3 text-sm text-slate-800">
          {msg}
        </p>
      )}

      {/* Intake */}
      <section className="mt-8">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Intake {c?.complete ? "✓ complete" : "— incomplete"}
        </h2>
        {c && !c.complete && c.missing_fields.length > 0 && (
          <p className="mt-2 text-sm text-amber-700">
            Missing fields: {c.missing_fields.join(", ")}.
          </p>
        )}
        {c && (
          <div className="mt-3">
            <div className="flex items-center justify-between">
              <div className="text-xs uppercase tracking-wide text-slate-500">
                {c.stage === "founder"
                  ? "Founder onboarding — start here (personal docs)"
                  : "Entity documents"}{" "}
                — *required
              </div>
              <button
                onClick={() =>
                  downloadFile(
                    `/clients/${id}/documents/request-pack`,
                    `${meta?.name ?? "client"}-document-request.pdf`,
                  ).catch((e) =>
                    setMsg(
                      e instanceof Error ? e.message : "Download failed",
                    ),
                  )
                }
                className="rounded-md border border-slate-300 px-2 py-1 text-xs"
              >
                Download request pack ↓
              </button>
            </div>
            <ul className="mt-2 divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white">
              {(c.required_documents ?? []).map((d) => (
                <li
                  key={d.key}
                  className="flex items-center justify-between p-3 text-sm"
                >
                  <span>
                    {d.label}
                    {d.mandatory && (
                      <span className="text-red-600" title="mandatory">
                        {" "}
                        *
                      </span>
                    )}
                    {d.needs_scan && (
                      <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-semibold text-amber-800">
                        scan
                      </span>
                    )}
                  </span>
                  <span className="flex items-center gap-3">
                    <button
                      onClick={() =>
                        downloadFile(
                          `/clients/${id}/documents/${d.key}/sample`,
                          `${d.key}-requirements.txt`,
                        ).catch((e) =>
                          setMsg(
                            e instanceof Error ? e.message : "Download failed",
                          ),
                        )
                      }
                      className="text-xs text-slate-500 underline"
                      title="Download a spec to send the client"
                    >
                      sample ↓
                    </button>
                    <span
                      className={
                        d.provided
                          ? "text-xs font-semibold text-green-700"
                          : "text-xs font-semibold text-slate-400"
                      }
                    >
                      {d.provided ? "provided ✓" : "missing"}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
        <textarea
          className="mt-3 h-40 w-full rounded-md border border-slate-300 p-2 font-mono text-xs"
          value={intakeText}
          onChange={(e) => setIntakeText(e.target.value)}
        />
        <div className="mt-2 flex gap-2">
          <button
            disabled={busy}
            onClick={saveIntake}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            Save intake
          </button>
          <button
            disabled={busy}
            onClick={() =>
              act(
                () =>
                  apiFetch(`/clients/${id}/intake/submit`, { method: "POST" }),
                "Intake submitted — client fully onboarded.",
              )
            }
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
          >
            Submit intake
          </button>
        </div>
      </section>

      {/* Documents */}
      <section className="mt-8">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Documents
        </h2>
        <ul className="mt-2 divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white">
          {docs.length === 0 && (
            <li className="p-3 text-sm text-slate-500">None yet.</li>
          )}
          {docs.map((d) => (
            <li key={d.id} className="flex justify-between p-3 text-sm">
              <span>{d.type}</span>
              <span className="text-slate-500">v{d.version}</span>
            </li>
          ))}
        </ul>
        <div className="mt-2 flex gap-2">
          <input
            className="flex-1 rounded-md border border-slate-300 px-3 py-1.5 text-sm"
            placeholder="Document type (e.g. ein_letter)"
            value={docType}
            onChange={(e) => setDocType(e.target.value)}
          />
          <button
            disabled={busy || !docType.trim()}
            onClick={() =>
              act(
                () =>
                  apiFetch(`/clients/${id}/documents`, {
                    method: "POST",
                    body: JSON.stringify({ type: docType }),
                  }),
                "Document registered.",
              ).then(() => setDocType(""))
            }
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            Add
          </button>
        </div>
      </section>

      {/* Run an agent */}
      <section className="mt-8">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Run an agent
        </h2>
        <div className="mt-2 flex gap-2">
          <select
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
            value={agent}
            onChange={(e) => setAgent(e.target.value)}
          >
            {AGENTS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
          <input
            className="flex-1 rounded-md border border-slate-300 px-3 py-1.5 text-sm"
            placeholder="Instruction (e.g. Plan the launch / File the FCC 499)"
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
          />
          <button
            disabled={busy || !instruction.trim()}
            onClick={runAgent}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            Run
          </button>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          Needs ANTHROPIC_API_KEY on the API; gated actions land in the
          approval queue.
        </p>
      </section>

      {/* Tasks */}
      <section className="mt-8">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Tasks
          </h2>
          <button
            disabled={busy}
            onClick={() =>
              act(
                () =>
                  apiFetch(`/clients/${id}/tasks/run-queued`, {
                    method: "POST",
                  }),
                "Swept queued tasks.",
              )
            }
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
          >
            Run all queued
          </button>
        </div>
        <ul className="mt-2 divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white">
          {tasks.length === 0 && (
            <li className="p-3 text-sm text-slate-500">No tasks.</li>
          )}
          {tasks.map((t) => (
            <li key={t.id} className="flex items-center justify-between p-3 text-sm">
              <span>
                <span className="font-medium">{t.agent}</span>{" "}
                <span className="text-slate-500">
                  {String(t.input?.objective ?? t.input?.instruction ?? "")}
                </span>
              </span>
              <span className="flex items-center gap-3">
                <span className="text-xs uppercase tracking-wide text-slate-500">
                  {t.status}
                </span>
                {t.status === "queued" && t.agent !== "pm" && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      act(
                        () =>
                          apiFetch(`/clients/${id}/tasks/${t.id}/run`, {
                            method: "POST",
                          }),
                        "Task run.",
                      )
                    }
                    className="rounded-md bg-slate-900 px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
                  >
                    Run
                  </button>
                )}
              </span>
            </li>
          ))}
        </ul>
      </section>

      {/* Audit trail */}
      <section className="mt-8">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Audit trail
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          Immutable, append-only — every queued / decided / executed action.
        </p>
        <ul className="mt-2 divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white">
          {audit.length === 0 && (
            <li className="p-3 text-sm text-slate-500">No audit entries.</li>
          )}
          {audit.map((e) => (
            <li key={e.id} className="flex justify-between gap-3 p-3 text-sm">
              <span>
                <span className="font-mono text-xs">{e.action}</span>{" "}
                <span className="text-slate-500">by {e.actor}</span>
              </span>
              <span className="whitespace-nowrap text-xs text-slate-400">
                {new Date(e.ts).toLocaleString()}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
