"use client";

import { useCallback, useEffect, useState } from "react";

import { apiFetch, downloadFile } from "@/lib/api";
import IntakeForm, { type IntakeFields } from "@/app/clients/[id]/IntakeForm";

type Usage = { client_name: string; stage: string; label: string | null };

type RequiredDoc = {
  key: string;
  label: string;
  mandatory: boolean;
  needs_scan: boolean;
  phase: string; // 'intake' or 'launch'
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
  size_bytes: number | null;
};

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export default function ClientPortalPage({
  params,
}: {
  params: { token: string };
}) {
  const tok = params.token;
  const base = `/client-portal/${tok}`;

  const [usage, setUsage] = useState<Usage | null>(null);
  const [intake, setIntake] = useState<IntakeStatus | null>(null);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [services, setServices] = useState<string[]>([]);
  const [credForm, setCredForm] = useState({
    service: "",
    username: "",
    secret: "",
  });
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [fatal, setFatal] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [u, i, d, s] = await Promise.all([
        apiFetch<Usage>(base),
        apiFetch<IntakeStatus>(`${base}/intake`),
        apiFetch<Doc[]>(`${base}/documents`),
        apiFetch<string[]>(`${base}/credentials/services`),
      ]);
      setUsage(u);
      setIntake(i);
      setDocs(d);
      setServices(s);
    } catch (e) {
      setFatal(e instanceof Error ? e.message : "This link is not available.");
    }
  }, [base]);

  useEffect(() => {
    load();
  }, [load]);

  async function act<T>(fn: () => Promise<T>, ok: string) {
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      await fn();
      setMsg(ok);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Action failed");
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
        apiFetch(`${base}/documents`, { method: "POST", body: form }),
      `Uploaded ${file.name}.`,
    );
  }

  if (fatal) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
        <div className="max-w-md rounded-lg border border-slate-200 bg-white p-8 text-center shadow-sm">
          <h1 className="text-lg font-semibold text-slate-900">
            Link unavailable
          </h1>
          <p className="mt-2 text-sm text-slate-600">{fatal}</p>
          <p className="mt-4 text-xs text-slate-400">
            Contact whoever sent you this link to get a new one.
          </p>
        </div>
      </div>
    );
  }

  if (!usage || !intake) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 text-sm text-slate-500">
        Loading…
      </div>
    );
  }

  const c = intake.completeness;

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-3xl px-4 py-5 sm:px-6">
          <p className="text-xs uppercase tracking-wide text-slate-500">
            Switchboard onboarding · {usage.label ?? "share"}
          </p>
          <h1 className="mt-1 text-2xl font-semibold text-slate-900">
            {usage.client_name}
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            Fill out the company details and upload the requested documents.
            We&apos;ll take it from here.
          </p>
        </div>
      </header>

      <main className="mx-auto max-w-3xl space-y-8 px-4 py-8 sm:px-6">
        {msg && (
          <p className="rounded-md border border-green-200 bg-green-50 p-3 text-sm text-green-800">
            {msg}
          </p>
        )}
        {err && (
          <p className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
            {err}
          </p>
        )}

        <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Documents we need
          </h2>
          {c.complete ? (
            <p className="mt-2 text-sm text-green-700">
              ✓ Everything is in. Thanks — the team will take it from here.
            </p>
          ) : (
            <p className="mt-2 text-sm text-slate-600">
              {c.missing_documents.length > 0
                ? `Still needed: ${c.missing_documents.length} document(s).`
                : "Intake is captured; please review the document list."}
            </p>
          )}
          <ul className="mt-3 divide-y divide-slate-200 rounded-lg border border-slate-200">
            {c.required_documents.map((d) => (
              <li
                key={d.key}
                className="flex flex-wrap items-center justify-between gap-2 p-3 text-sm"
              >
                <span>
                  {d.label}
                  {d.mandatory && <span className="text-red-600"> *</span>}
                  {d.phase === "launch" && !d.provided && (
                    <span
                      className="ml-2 rounded bg-blue-100 px-1.5 py-0.5 text-xs font-semibold text-blue-800"
                      title="Your team prepares this during the launch — not needed now"
                    >
                      later
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
                        `${base}/documents/${d.key}/sample`,
                        `${d.key}-requirements.txt`,
                      ).catch((e) =>
                        setErr(
                          e instanceof Error ? e.message : "Download failed",
                        ),
                      )
                    }
                    className="text-xs text-slate-500 underline"
                  >
                    what we need ↓
                  </button>
                  <label
                    className={
                      "cursor-pointer rounded-md px-2 py-1 text-xs font-medium " +
                      (d.provided
                        ? "border border-slate-300 text-slate-600 hover:bg-slate-50"
                        : "bg-slate-900 text-white hover:bg-slate-800")
                    }
                  >
                    {d.provided ? "replace" : "upload"}
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
                        : d.phase === "launch"
                          ? "text-xs font-semibold text-blue-700"
                          : "text-xs font-semibold text-slate-400"
                    }
                  >
                    {d.provided
                      ? "sent ✓"
                      : d.phase === "launch"
                        ? "later"
                        : "missing"}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </section>

        <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Company details
          </h2>
          <p className="mt-1 text-xs text-slate-500">
            These details power every filing the team makes for you. Capture
            them once; we&apos;ll never re-ask.
          </p>
          <IntakeForm
            intake={(intake.intake ?? null) as IntakeFields | null}
            busy={busy}
            onSave={(payload) =>
              act(
                () =>
                  apiFetch(`${base}/intake`, {
                    method: "PUT",
                    body: JSON.stringify(payload),
                  }),
                "Details saved.",
              )
            }
          />
        </section>

        <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Portal logins (optional)
          </h2>
          <p className="mt-1 text-xs text-slate-500">
            If you already have FCC CORES / state PUC / carrier portal logins,
            paste them here. Secrets are encrypted on receipt; the team can use
            them on your behalf, but never sees the raw value.
          </p>
          {services.length > 0 && (
            <p className="mt-2 text-xs text-slate-500">
              Already on file:{" "}
              <span className="font-medium text-slate-700">
                {services.join(", ")}
              </span>
            </p>
          )}
          <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
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
              autoComplete="new-password"
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm"
              placeholder="Password / API token"
              value={credForm.secret}
              onChange={(e) =>
                setCredForm({ ...credForm, secret: e.target.value })
              }
            />
          </div>
          <button
            disabled={
              busy || !credForm.service.trim() || !credForm.secret.trim()
            }
            onClick={() =>
              act(
                () =>
                  apiFetch(`${base}/credentials`, {
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
            className="mt-3 rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            Save credential
          </button>
        </section>

        {docs.length > 0 && (
          <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
              What you&apos;ve sent
            </h2>
            <ul className="mt-2 divide-y divide-slate-200 rounded-lg border border-slate-200">
              {docs.map((d) => (
                <li
                  key={d.id}
                  className="flex items-center justify-between p-3 text-sm"
                >
                  <span>
                    <span className="font-medium">{d.type}</span>{" "}
                    <span className="text-xs text-slate-500">v{d.version}</span>
                    {d.filename && (
                      <span className="ml-2 text-xs text-slate-500">
                        {d.filename}
                        {typeof d.size_bytes === "number" &&
                          ` · ${formatBytes(d.size_bytes)}`}
                      </span>
                    )}
                  </span>
                  {d.s3_key && (
                    <button
                      onClick={() =>
                        downloadFile(
                          `${base}/documents/${d.id}/download`,
                          d.filename ?? `${d.type}-v${d.version}`,
                        ).catch((e) =>
                          setErr(
                            e instanceof Error ? e.message : "Download failed",
                          ),
                        )
                      }
                      className="text-xs text-slate-700 underline"
                    >
                      download ↓
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}

        <p className="text-center text-xs text-slate-400">
          Powered by Switchboard · this link is private to your company.
        </p>
      </main>
    </div>
  );
}
