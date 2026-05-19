"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { apiFetch, downloadFile, fetchBlob } from "@/lib/api";
import AppShell from "@/app/AppShell";
import IntakeForm, { type IntakeFields } from "./IntakeForm";

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
type Doc = {
  id: string;
  type: string;
  version: number;
  s3_key: string | null;
  filename: string | null;
  mime: string | null;
  size_bytes: number | null;
  created_at: string;
};
type Cred = {
  id: string;
  service: string;
  username: string | null;
  expires_at: string | null;
  last_accessed_at: string | null;
};
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

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function DocPreviewModal({
  doc,
  clientId,
  onClose,
}: {
  doc: Doc;
  clientId: string;
  onClose: () => void;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    (async () => {
      try {
        const blob = await fetchBlob(
          `/clients/${clientId}/documents/${doc.id}/download`,
        );
        if (!active) return;
        const isText =
          (doc.mime ?? "").startsWith("text/") ||
          (doc.mime ?? "") === "application/json";
        if (isText) {
          setText(await blob.text());
        } else {
          objectUrl = URL.createObjectURL(blob);
          setUrl(objectUrl);
        }
      } catch (err) {
        if (active)
          setError(err instanceof Error ? err.message : "Preview failed");
      }
    })();
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [doc.id, doc.mime, clientId]);

  const mime = doc.mime ?? "";
  const isImage = mime.startsWith("image/");
  const isPdf = mime === "application/pdf";

  return (
    <div
      className="fixed inset-0 z-20 flex items-center justify-center bg-slate-900/60 p-4"
      onClick={onClose}
    >
      <div
        className="flex max-h-full w-full max-w-4xl flex-col overflow-hidden rounded-lg bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-slate-900">
              {doc.filename ?? `${doc.type}-v${doc.version}`}
            </div>
            <div className="text-xs text-slate-500">
              {doc.type} · v{doc.version}
              {typeof doc.size_bytes === "number" &&
                ` · ${formatBytes(doc.size_bytes)}`}
              {doc.mime && ` · ${doc.mime}`}
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded-md px-2 py-1 text-sm text-slate-600 hover:bg-slate-100"
          >
            close ✕
          </button>
        </div>
        <div className="flex min-h-[400px] flex-1 items-center justify-center overflow-auto bg-slate-50 p-2">
          {error && <p className="text-sm text-red-600">{error}</p>}
          {!error && !url && text === null && (
            <p className="text-sm text-slate-500">Loading preview…</p>
          )}
          {url && isImage && (
            <img src={url} alt={doc.filename ?? doc.type} className="max-h-[70vh]" />
          )}
          {url && isPdf && (
            <iframe
              src={url}
              title={doc.filename ?? doc.type}
              className="h-[70vh] w-full"
            />
          )}
          {url && !isImage && !isPdf && (
            <div className="p-6 text-center text-sm text-slate-600">
              <p>Preview not available for this file type.</p>
              <button
                onClick={() =>
                  downloadFile(
                    `/clients/${clientId}/documents/${doc.id}/download`,
                    doc.filename ?? `${doc.type}-v${doc.version}`,
                  )
                }
                className="mt-3 rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white"
              >
                Download
              </button>
            </div>
          )}
          {text !== null && (
            <pre className="max-h-[70vh] w-full overflow-auto whitespace-pre-wrap rounded border border-slate-200 bg-white p-3 text-xs text-slate-800">
              {text}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ClientDetailPage() {
  const { id } = useParams<{ id: string }>();

  const [meta, setMeta] = useState<ClientMeta | null>(null);
  const [intake, setIntake] = useState<IntakeStatus | null>(null);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [audit, setAudit] = useState<Audit[]>([]);
  const [creds, setCreds] = useState<Cred[]>([]);
  const [credForm, setCredForm] = useState({
    service: "",
    username: "",
    secret: "",
  });
  const [docType, setDocType] = useState("");
  const [docFile, setDocFile] = useState<File | null>(null);
  const [previewDoc, setPreviewDoc] = useState<Doc | null>(null);
  const [agent, setAgent] = useState("compliance");
  const [instruction, setInstruction] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [clients, st, d, t, au, cr] = await Promise.all([
        apiFetch<ClientMeta[]>("/clients"),
        apiFetch<IntakeStatus>(`/clients/${id}/intake`),
        apiFetch<Doc[]>(`/clients/${id}/documents`),
        apiFetch<Task[]>(`/clients/${id}/tasks`),
        apiFetch<Audit[]>(`/clients/${id}/audit`),
        apiFetch<Cred[]>(`/clients/${id}/credentials`),
      ]);
      setMeta(clients.find((c) => c.id === id) ?? null);
      setIntake(st);
      setDocs(d);
      setTasks(t);
      setAudit(au);
      setCreds(cr);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Failed to load");
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

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

  async function uploadDocument(typeKey: string, file: File) {
    const form = new FormData();
    form.set("type", typeKey);
    form.set("file", file);
    await act(
      () =>
        apiFetch(`/clients/${id}/documents`, {
          method: "POST",
          body: form,
        }),
      `Uploaded ${file.name} as ${typeKey}.`,
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
    <AppShell>
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">
            {meta?.name ?? "Client"}
          </h1>
          {meta && (
            <p className="mt-1 text-xs uppercase tracking-wide text-slate-500">
              stage: {meta.stage}
            </p>
          )}
        </div>
        <Link
          href="/approvals"
          className="text-sm text-slate-600 hover:text-slate-900 hover:underline"
        >
          Approval queue →
        </Link>
      </div>

      {msg && (
        <p className="mt-4 rounded-md border border-slate-200 bg-white p-3 text-sm text-slate-800 shadow-sm">
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
            <ul className="mt-2 divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white shadow-sm">
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
                    <label
                      className={
                        "cursor-pointer rounded-md px-2 py-1 text-xs font-medium " +
                        (d.provided
                          ? "border border-slate-300 text-slate-600 hover:bg-slate-50"
                          : "bg-slate-900 text-white hover:bg-slate-800")
                      }
                      title={
                        d.provided
                          ? "Replace the uploaded file"
                          : "Upload a file for this document"
                      }
                    >
                      {d.provided ? "replace ↑" : "+ upload"}
                      <input
                        type="file"
                        className="hidden"
                        disabled={busy}
                        onChange={(e) => {
                          const f = e.target.files?.[0];
                          e.target.value = "";
                          if (f) uploadDocument(d.key, f);
                        }}
                      />
                    </label>
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
        <IntakeForm
          intake={(intake?.intake ?? null) as IntakeFields | null}
          busy={busy}
          onSave={(payload) =>
            act(
              () =>
                apiFetch(`/clients/${id}/intake`, {
                  method: "PUT",
                  body: JSON.stringify(payload),
                }),
              "Intake saved.",
            )
          }
        />
        <button
          disabled={busy}
          onClick={() =>
            act(
              () =>
                apiFetch(`/clients/${id}/intake/submit`, { method: "POST" }),
              "Intake submitted — client fully onboarded.",
            )
          }
          className="mt-3 rounded-md border border-slate-300 px-3 py-1.5 text-sm"
        >
          Submit intake
        </button>
      </section>

      {/* Documents */}
      <section className="mt-8">
        <div className="flex items-baseline justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Documents
          </h2>
          <label className="cursor-pointer rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-800">
            + Add document
            <input
              type="file"
              className="hidden"
              disabled={busy}
              onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (!f) return;
                // Pre-fill the type input + focus it so the operator
                // can name the new doc; the bulk form below handles
                // the actual upload on submit.
                setDocFile(f);
                document.getElementById("doc-type-input")?.focus();
              }}
            />
          </label>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          Mandated documents have their own + upload buttons in the
          intake list above. Use the form below for ad-hoc uploads
          (e.g., supporting attachments). Storage is content-addressed —
          uploading the same bytes twice de-dupes on disk, and
          re-uploading a type bumps its version.
        </p>
        <ul className="mt-2 divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white shadow-sm">
          {docs.length === 0 && (
            <li className="p-3 text-sm text-slate-500">None yet.</li>
          )}
          {docs.map((d) => (
            <li
              key={d.id}
              id={`doc-${d.id}`}
              className="flex items-center justify-between p-3 text-sm target:bg-amber-50"
            >
              <span className="flex flex-col">
                <span>
                  <span className="font-medium">{d.type}</span>{" "}
                  <span className="text-xs text-slate-500">v{d.version}</span>
                </span>
                {d.filename && (
                  <button
                    onClick={() => d.s3_key && setPreviewDoc(d)}
                    disabled={!d.s3_key}
                    className="text-left text-xs text-slate-500 underline-offset-2 hover:text-slate-800 hover:underline disabled:no-underline disabled:hover:text-slate-500"
                  >
                    {d.filename}
                    {typeof d.size_bytes === "number" &&
                      ` · ${formatBytes(d.size_bytes)}`}
                  </button>
                )}
              </span>
              {d.s3_key ? (
                <span className="flex items-center gap-3">
                  <button
                    onClick={() => setPreviewDoc(d)}
                    className="text-xs text-slate-700 underline"
                  >
                    preview
                  </button>
                  <button
                    onClick={() =>
                      downloadFile(
                        `/clients/${id}/documents/${d.id}/download`,
                        d.filename ?? `${d.type}-v${d.version}`,
                      ).catch((e) =>
                        setMsg(
                          e instanceof Error ? e.message : "Download failed",
                        ),
                      )
                    }
                    className="text-xs text-slate-700 underline"
                  >
                    download ↓
                  </button>
                </span>
              ) : (
                <span className="text-xs italic text-slate-400">no file</span>
              )}
            </li>
          ))}
        </ul>
        <form
          className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-[1fr_auto_auto]"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!docType.trim() || !docFile) return;
            const form = new FormData();
            form.set("type", docType.trim());
            form.set("file", docFile);
            await act(
              () =>
                apiFetch(`/clients/${id}/documents`, {
                  method: "POST",
                  body: form,
                }),
              `Uploaded ${docFile.name}.`,
            );
            setDocType("");
            setDocFile(null);
            (document.getElementById(
              "doc-upload-file",
            ) as HTMLInputElement | null)?.value &&
              ((document.getElementById(
                "doc-upload-file",
              ) as HTMLInputElement).value = "");
          }}
        >
          <input
            id="doc-type-input"
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
            placeholder="Document type (e.g. ein_letter)"
            value={docType}
            onChange={(e) => setDocType(e.target.value)}
          />
          <input
            id="doc-upload-file"
            type="file"
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
            onChange={(e) => setDocFile(e.target.files?.[0] ?? null)}
          />
          <button
            type="submit"
            disabled={busy || !docType.trim() || !docFile}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            Upload
          </button>
        </form>
      </section>

      {/* Credentials vault */}
      <section className="mt-8">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Credentials vault
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          External logins (FCC CORES, IRS, state PUC, bank, carrier portals,
          …). Encrypted at rest. Agents can see <i>which</i> services are on
          file (audited) — they can never see the secret; the platform uses
          it on the client's behalf.
        </p>
        <ul className="mt-2 divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white shadow-sm">
          {creds.length === 0 && (
            <li className="p-3 text-sm text-slate-500">No credentials yet.</li>
          )}
          {creds.map((c) => (
            <li key={c.id} className="flex items-center justify-between p-3 text-sm">
              <span>
                <span className="font-medium">{c.service}</span>
                {c.username && (
                  <span className="ml-2 text-slate-500">({c.username})</span>
                )}
              </span>
              <span className="flex items-center gap-3">
                {c.expires_at && (
                  <span className="text-xs text-slate-500">
                    expires {new Date(c.expires_at).toLocaleDateString()}
                  </span>
                )}
                <button
                  disabled={busy}
                  onClick={() =>
                    act(
                      () =>
                        apiFetch(`/clients/${id}/credentials/${c.id}`, {
                          method: "DELETE",
                        }),
                      `Removed ${c.service}.`,
                    )
                  }
                  className="text-xs text-red-600 underline"
                >
                  delete
                </button>
              </span>
            </li>
          ))}
        </ul>
        <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
          <input
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
            placeholder="Service (e.g. fcc_cores)"
            value={credForm.service}
            onChange={(e) =>
              setCredForm({ ...credForm, service: e.target.value })
            }
          />
          <input
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
            placeholder="Username (optional)"
            value={credForm.username}
            onChange={(e) =>
              setCredForm({ ...credForm, username: e.target.value })
            }
          />
          <input
            type="password"
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
            placeholder="Secret"
            value={credForm.secret}
            onChange={(e) =>
              setCredForm({ ...credForm, secret: e.target.value })
            }
            autoComplete="new-password"
          />
        </div>
        <button
          disabled={busy || !credForm.service.trim() || !credForm.secret.trim()}
          onClick={() =>
            act(
              () =>
                apiFetch(`/clients/${id}/credentials`, {
                  method: "POST",
                  body: JSON.stringify({
                    service: credForm.service,
                    username: credForm.username || null,
                    secret: credForm.secret,
                  }),
                }),
              `${credForm.service} stored.`,
            ).then(() =>
              setCredForm({ service: "", username: "", secret: "" }),
            )
          }
          className="mt-2 rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          Store credential
        </button>
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
        <ul className="mt-2 divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white shadow-sm">
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
        <div className="flex items-baseline justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Audit trail
          </h2>
          <button
            onClick={() =>
              downloadFile(
                `/clients/${id}/audit.csv`,
                `${meta?.name ?? "client"}-audit.csv`,
              ).catch((e) =>
                setMsg(e instanceof Error ? e.message : "Export failed"),
              )
            }
            className="rounded-md border border-slate-300 px-2 py-1 text-xs"
          >
            Export CSV ↓
          </button>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          Immutable, append-only — every queued / decided / executed action.
        </p>
        <ul className="mt-2 divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white shadow-sm">
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

      {previewDoc && (
        <DocPreviewModal
          doc={previewDoc}
          clientId={id}
          onClose={() => setPreviewDoc(null)}
        />
      )}
    </AppShell>
  );
}
