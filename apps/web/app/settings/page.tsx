"use client";

import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import AppShell from "@/app/AppShell";

type Me = {
  id: string;
  email: string;
  name: string;
  created_at: string;
};

type SystemStatus = {
  environment: string;
  agent_model: string;
  anthropic: boolean;
  smtp_configured: boolean;
  smtp_host: string | null;
  smtp_port: number;
  smtp_user: string | null;
  smtp_password_set: boolean;
  smtp_from: string | null;
  smtp_use_tls: boolean;
  app_base_url: string | null;
  documents_dir: string;
  portal_integration_backend: string;
  app_version: string;
  git_commit: string | null;
  git_branch: string | null;
};

type UpdateStatus = {
  repo: string;
  current_commit: string | null;
  update_pending: boolean;
  update_pending_meta: { requested_by: string; requested_at: string } | null;
  up_to_date: boolean | null;
  latest_available: {
    sha: string;
    message: string;
    author: string;
    date: string;
    url: string;
  } | null;
  check_error?: string | null;
};

type CertificateInfo =
  | {
      available: true;
      host: string;
      subject_cn: string | null;
      issuer_o: string | null;
      issuer_cn: string | null;
      not_after: string;
      days_until_expiry: number;
      alt_names: string[];
      auto_renewed_by: string;
    }
  | { available: false; reason: string };

type HealthCheck = {
  overall_ok: boolean;
  checks: Record<string, { ok: boolean; note?: string; error?: string; path?: string }>;
};

function ReadyRow({
  label,
  ok,
  detail,
}: {
  label: string;
  ok: boolean;
  detail?: string;
}) {
  return (
    <div className="flex items-baseline gap-2">
      <span
        className={
          ok
            ? "rounded bg-green-100 px-1.5 py-0.5 text-xs font-semibold text-green-800"
            : "rounded bg-amber-100 px-1.5 py-0.5 text-xs font-semibold text-amber-800"
        }
      >
        {ok ? "ready" : "not set"}
      </span>
      <span className="text-sm font-medium text-slate-800">{label}</span>
      {detail && <span className="truncate text-xs text-slate-500">— {detail}</span>}
    </div>
  );
}

const INPUT_CLS =
  "w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-slate-500 focus:outline-none";

export default function SettingsPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [updateInfo, setUpdateInfo] = useState<UpdateStatus | null>(null);
  const [updateLoading, setUpdateLoading] = useState(false);
  const [cert, setCert] = useState<CertificateInfo | null>(null);
  const [certLoading, setCertLoading] = useState(false);
  const [health, setHealth] = useState<HealthCheck | null>(null);
  const [healthLoading, setHealthLoading] = useState(false);
  const [name, setName] = useState("");
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");

  // Platform-config editors. Empty means "no change"; the operator
  // types a new value into the field they want to update.
  const [anthropicKey, setAnthropicKey] = useState("");
  const [agentModel, setAgentModel] = useState("");
  const [smtpHost, setSmtpHost] = useState("");
  const [smtpPort, setSmtpPort] = useState("");
  const [smtpUser, setSmtpUser] = useState("");
  const [smtpPassword, setSmtpPassword] = useState("");
  const [smtpFrom, setSmtpFrom] = useState("");
  const [smtpUseTls, setSmtpUseTls] = useState(true);
  const [appBaseUrl, setAppBaseUrl] = useState("");
  const [testTo, setTestTo] = useState("");

  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    const [u, s] = await Promise.all([
      apiFetch<Me>("/auth/me"),
      apiFetch<SystemStatus>("/system/status"),
    ]);
    setMe(u);
    setName(u.name);
    setStatus(s);
    // Fire-and-forget the three diagnostic loads so the page paints fast.
    loadUpdate();
    loadCert();
    // Pre-fill non-secret fields so the operator can edit them in
    // place instead of retyping. Secrets stay blank.
    setAgentModel(s.agent_model ?? "");
    setSmtpHost(s.smtp_host ?? "");
    setSmtpPort(s.smtp_port ? String(s.smtp_port) : "");
    setSmtpUser(s.smtp_user ?? "");
    setSmtpFrom(s.smtp_from ?? "");
    setSmtpUseTls(s.smtp_use_tls);
    setAppBaseUrl(s.app_base_url ?? "");
    setTestTo((prev) => prev || u.email);
  }

  useEffect(() => {
    (async () => {
      try {
        await refresh();
      } catch (e) {
        setErr(e instanceof Error ? e.message : "Failed to load profile");
      }
    })();
  }, []);

  async function saveProfile(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      const u = await apiFetch<Me>("/auth/me", {
        method: "PUT",
        body: JSON.stringify({ name }),
      });
      setMe(u);
      setMsg("Profile saved.");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  async function changePassword(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    setErr(null);
    if (next !== confirm) {
      setBusy(false);
      setErr("New password and confirmation do not match.");
      return;
    }
    if (next.length < 8) {
      setBusy(false);
      setErr("New password must be at least 8 characters.");
      return;
    }
    try {
      await apiFetch("/auth/change-password", {
        method: "POST",
        body: JSON.stringify({
          current_password: current,
          new_password: next,
        }),
      });
      setMsg("Password changed.");
      setCurrent("");
      setNext("");
      setConfirm("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Password change failed");
    } finally {
      setBusy(false);
    }
  }

  async function savePlatformConfig(
    payload: Record<string, string | number | boolean | null>,
    successMsg: string,
  ) {
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      await apiFetch("/system/config", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      setMsg(successMsg);
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  async function saveAnthropic(e: React.FormEvent) {
    e.preventDefault();
    if (!anthropicKey.trim() && !agentModel.trim()) {
      setErr("Enter an Anthropic key or a model to save.");
      return;
    }
    const payload: Record<string, string> = {};
    if (anthropicKey.trim()) payload.anthropic_api_key = anthropicKey.trim();
    if (agentModel.trim()) payload.agent_model = agentModel.trim();
    await savePlatformConfig(payload, "Anthropic settings saved.");
    setAnthropicKey("");
  }

  async function clearAnthropicKey() {
    if (!confirm) {
      // (unused locally — the "confirm" var is for password; use window.confirm)
    }
    if (!window.confirm("Clear the stored Anthropic API key?")) return;
    await savePlatformConfig(
      { anthropic_api_key: "" },
      "Anthropic key cleared (falling back to env if set).",
    );
  }

  async function saveSmtp(e: React.FormEvent) {
    e.preventDefault();
    const payload: Record<string, string | number | boolean> = {
      smtp_host: smtpHost.trim(),
      smtp_user: smtpUser.trim(),
      smtp_from: smtpFrom.trim(),
      smtp_use_tls: smtpUseTls,
      app_base_url: appBaseUrl.trim(),
    };
    if (smtpPort.trim()) payload.smtp_port = Number(smtpPort);
    // Only send the password if the operator typed a new one.
    if (smtpPassword.trim()) payload.smtp_password = smtpPassword.trim();
    await savePlatformConfig(payload, "SMTP settings saved.");
    setSmtpPassword("");
  }

  async function loadUpdate() {
    setUpdateLoading(true);
    try {
      setUpdateInfo(await apiFetch<UpdateStatus>("/system/update"));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Update check failed");
    } finally {
      setUpdateLoading(false);
    }
  }

  async function applyUpdate() {
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      const r = await apiFetch<{ note: string }>("/system/update/request", {
        method: "POST",
      });
      setMsg(r.note);
      await loadUpdate();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not queue update");
    } finally {
      setBusy(false);
    }
  }

  async function cancelUpdate() {
    setBusy(true);
    try {
      await apiFetch("/system/update/request", { method: "DELETE" });
      setMsg("Update request cancelled.");
      await loadUpdate();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Cancel failed");
    } finally {
      setBusy(false);
    }
  }

  async function loadCert() {
    setCertLoading(true);
    try {
      setCert(await apiFetch<CertificateInfo>("/system/certificate"));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Cert check failed");
    } finally {
      setCertLoading(false);
    }
  }

  async function runSelfTest() {
    setHealthLoading(true);
    setMsg(null);
    setErr(null);
    try {
      setHealth(await apiFetch<HealthCheck>("/health/detailed"));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Self-test failed");
    } finally {
      setHealthLoading(false);
    }
  }

  async function sendTestEmail() {
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      const r = await apiFetch<{ status: string }>(
        "/system/config/test-email",
        { method: "POST", body: JSON.stringify({ to: testTo }) },
      );
      setMsg(`Test email: ${r.status}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Test failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell>
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold text-slate-900">Settings</h1>
        {me && (
          <span className="text-xs text-slate-500">
            member since {new Date(me.created_at).toLocaleDateString()}
          </span>
        )}
      </div>

      {msg && (
        <p className="mt-4 rounded-md border border-green-200 bg-green-50 p-3 text-sm text-green-800">
          {msg}
        </p>
      )}
      {err && (
        <p className="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {err}
        </p>
      )}

      {status && (
        <section className="mt-8 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Platform readiness
          </h2>
          <dl className="mt-3 grid grid-cols-1 gap-y-2 text-sm sm:grid-cols-2">
            <ReadyRow
              label="Anthropic API key"
              ok={status.anthropic}
              detail={status.anthropic ? `model: ${status.agent_model}` : "agent runs return 503 until set"}
            />
            <ReadyRow
              label="Email notifications"
              ok={status.smtp_configured}
              detail={
                status.smtp_configured
                  ? `${status.smtp_host} (from ${status.smtp_from})`
                  : "operator works from the in-app badge only"
              }
            />
            <ReadyRow
              label="Portal integration"
              ok={status.portal_integration_backend !== "demo"}
              detail={`backend: ${status.portal_integration_backend}`}
            />
            <ReadyRow
              label="Public URL"
              ok={!!status.app_base_url}
              detail={status.app_base_url ?? "email links will be relative"}
            />
            <div className="text-xs text-slate-500 sm:col-span-2">
              <span className="font-medium text-slate-700">Environment:</span>{" "}
              {status.environment} ·{" "}
              <span className="font-medium text-slate-700">
                Document storage:
              </span>{" "}
              <code className="text-xs">{status.documents_dir}</code>
            </div>
            <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-slate-200 pt-3 text-xs text-slate-500 sm:col-span-2">
              <span>
                <span className="font-medium text-slate-700">Version:</span>{" "}
                <code className="rounded bg-slate-100 px-1.5 py-0.5">
                  v{status.app_version}
                </code>
              </span>
              {status.git_commit && (
                <span>
                  <span className="font-medium text-slate-700">Commit:</span>{" "}
                  <code className="rounded bg-slate-100 px-1.5 py-0.5">
                    {status.git_commit}
                  </code>
                  {status.git_branch && status.git_branch !== "main" && (
                    <span className="ml-1">
                      ({status.git_branch})
                    </span>
                  )}
                </span>
              )}
              <a
                href="https://github.com/steven-patrick18/switchboard/blob/main/CHANGELOG.md"
                target="_blank"
                rel="noopener noreferrer"
                className="text-slate-600 underline hover:text-slate-900"
              >
                What&apos;s new ↗
              </a>
            </div>
          </dl>
        </section>
      )}

      {/* --- System updates --- */}
      <section className="mt-8 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex items-baseline justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            System updates
          </h2>
          <button
            onClick={loadUpdate}
            disabled={updateLoading}
            className="rounded-md border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50 disabled:opacity-50"
          >
            {updateLoading ? "Checking…" : "Check for updates"}
          </button>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          Compares the live commit against the latest on GitHub. Clicking
          Apply update writes a sentinel file the host&apos;s update cron
          picks up on its next tick (5 min by default). VPS only — on
          Railway, push to <code>main</code> auto-deploys.
        </p>

        {updateInfo == null && !updateLoading && (
          <p className="mt-3 text-xs text-slate-400">
            Click Check for updates to see what&apos;s available.
          </p>
        )}

        {updateInfo && (
          <div className="mt-3 space-y-2 text-sm">
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="text-xs text-slate-500">Repository:</span>
              <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">
                {updateInfo.repo}
              </code>
            </div>
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="text-xs text-slate-500">Live:</span>
              <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">
                {updateInfo.current_commit ?? "unknown (dev?)"}
              </code>
              <span className="text-xs text-slate-500">Latest:</span>
              <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">
                {updateInfo.latest_available?.sha ?? "n/a"}
              </code>
              {updateInfo.up_to_date === true && (
                <span className="rounded bg-green-100 px-1.5 py-0.5 text-xs font-semibold text-green-800">
                  up to date
                </span>
              )}
              {updateInfo.up_to_date === false && (
                <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-semibold text-amber-800">
                  update available
                </span>
              )}
            </div>
            {updateInfo.latest_available && (
              <p className="text-xs text-slate-600">
                <span className="font-medium">Latest commit:</span>{" "}
                {updateInfo.latest_available.message}{" "}
                <span className="text-slate-400">
                  by {updateInfo.latest_available.author},{" "}
                  {new Date(
                    updateInfo.latest_available.date,
                  ).toLocaleString()}
                </span>{" "}
                <a
                  href={updateInfo.latest_available.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-slate-700 underline"
                >
                  view ↗
                </a>
              </p>
            )}
            {updateInfo.check_error && (
              <p className="text-xs text-red-600">
                Could not reach GitHub: {updateInfo.check_error}
              </p>
            )}

            {updateInfo.update_pending && (
              <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
                <p className="font-semibold">Update queued.</p>
                {updateInfo.update_pending_meta && (
                  <p className="mt-1">
                    Requested by{" "}
                    {updateInfo.update_pending_meta.requested_by} at{" "}
                    {new Date(
                      updateInfo.update_pending_meta.requested_at,
                    ).toLocaleString()}
                    . The host&apos;s update cron will run on its next
                    tick.
                  </p>
                )}
                <button
                  onClick={cancelUpdate}
                  disabled={busy}
                  className="mt-2 rounded-md border border-amber-300 px-2 py-1 text-xs hover:bg-amber-100 disabled:opacity-50"
                >
                  Cancel
                </button>
              </div>
            )}

            <div className="flex flex-wrap gap-2 pt-2">
              <button
                onClick={applyUpdate}
                disabled={
                  busy ||
                  !updateInfo.latest_available ||
                  updateInfo.up_to_date === true ||
                  updateInfo.update_pending
                }
                className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
              >
                Apply update
              </button>
              <a
                href="https://github.com/steven-patrick18/switchboard/blob/main/CHANGELOG.md"
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50"
              >
                Changelog ↗
              </a>
            </div>
          </div>
        )}
      </section>

      {/* --- HTTPS certificate --- */}
      <section className="mt-8 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex items-baseline justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            HTTPS certificate
          </h2>
          <button
            onClick={loadCert}
            disabled={certLoading}
            className="rounded-md border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50 disabled:opacity-50"
          >
            {certLoading ? "Checking…" : "Refresh"}
          </button>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          Reads the live cert from your public URL. On the VPS deploy,
          Caddy auto-renews 30 days before expiry — you shouldn&apos;t
          have to do anything.
        </p>
        {cert == null && !certLoading && (
          <p className="mt-3 text-xs text-slate-400">Loading…</p>
        )}
        {cert && !cert.available && (
          <p className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
            {cert.reason}
          </p>
        )}
        {cert && cert.available && (
          <dl className="mt-3 grid grid-cols-1 gap-y-2 text-sm sm:grid-cols-2">
            <div className="flex items-baseline gap-2">
              <span className="text-xs text-slate-500">Domain:</span>
              <code className="text-sm">{cert.host}</code>
            </div>
            <div className="flex items-baseline gap-2">
              <span className="text-xs text-slate-500">Issuer:</span>
              <span className="text-sm">
                {cert.issuer_o ?? cert.issuer_cn ?? "unknown"}
              </span>
            </div>
            <div className="flex items-baseline gap-2">
              <span className="text-xs text-slate-500">Expires:</span>
              <span className="text-sm">
                {new Date(cert.not_after).toLocaleString()}
              </span>
              <span
                className={
                  "rounded px-1.5 py-0.5 text-xs font-semibold " +
                  (cert.days_until_expiry > 14
                    ? "bg-green-100 text-green-800"
                    : cert.days_until_expiry > 3
                      ? "bg-amber-100 text-amber-800"
                      : "bg-red-100 text-red-800")
                }
              >
                in {cert.days_until_expiry} days
              </span>
            </div>
            <div className="flex items-baseline gap-2">
              <span className="text-xs text-slate-500">Renewal:</span>
              <span className="text-sm">{cert.auto_renewed_by}</span>
            </div>
            {cert.alt_names.length > 0 && (
              <div className="sm:col-span-2">
                <span className="text-xs text-slate-500">Covers:</span>{" "}
                <span className="font-mono text-xs text-slate-700">
                  {cert.alt_names.join(", ")}
                </span>
              </div>
            )}
          </dl>
        )}
      </section>

      {/* --- Self-test --- */}
      <section className="mt-8 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex items-baseline justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            System self-test
          </h2>
          <button
            onClick={runSelfTest}
            disabled={healthLoading}
            className="rounded-md bg-slate-900 px-3 py-1 text-xs font-medium text-white hover:bg-slate-800 disabled:opacity-50"
          >
            {healthLoading ? "Running…" : "Run self-test"}
          </button>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          Verifies each subsystem (database, document storage, Anthropic
          key, SMTP, update channel). Read-only; safe to run any time.
        </p>
        {health && (
          <div className="mt-3 space-y-1 text-sm">
            <div className="flex items-baseline gap-2">
              <span
                className={
                  "rounded px-1.5 py-0.5 text-xs font-semibold " +
                  (health.overall_ok
                    ? "bg-green-100 text-green-800"
                    : "bg-amber-100 text-amber-800")
                }
              >
                {health.overall_ok ? "all systems ok" : "some checks failing"}
              </span>
            </div>
            <ul className="mt-2 divide-y divide-slate-200 rounded-md border border-slate-200">
              {Object.entries(health.checks).map(([name, c]) => (
                <li key={name} className="flex items-baseline gap-3 p-2 text-xs">
                  <span
                    className={
                      "rounded px-1.5 py-0.5 font-semibold " +
                      (c.ok
                        ? "bg-green-100 text-green-800"
                        : "bg-red-100 text-red-800")
                    }
                  >
                    {c.ok ? "ok" : "fail"}
                  </span>
                  <span className="font-mono text-sm">{name}</span>
                  {c.note && <span className="text-slate-500">— {c.note}</span>}
                  {c.error && (
                    <span className="text-red-600">— {c.error}</span>
                  )}
                  {c.path && (
                    <code className="text-slate-400">{c.path}</code>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <section className="mt-8 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Anthropic (Claude) API
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          Get a key from <code>console.anthropic.com</code>. Set a monthly
          spend cap there too. The key is stored encrypted; only its
          presence is ever exposed back through this UI.
        </p>
        <form className="mt-3 space-y-3" onSubmit={saveAnthropic}>
          <div>
            <label className="block text-xs text-slate-500">
              API key
              {status?.anthropic && (
                <span className="ml-2 rounded bg-green-100 px-1.5 py-0.5 text-xs font-semibold text-green-800">
                  set
                </span>
              )}
            </label>
            <input
              type="password"
              autoComplete="off"
              className={`mt-1 ${INPUT_CLS}`}
              placeholder={status?.anthropic ? "•••••••• (leave blank to keep)" : "sk-ant-..."}
              value={anthropicKey}
              onChange={(e) => setAnthropicKey(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500">
              Default agent model
            </label>
            <input
              className={`mt-1 ${INPUT_CLS}`}
              placeholder="claude-opus-4-7"
              value={agentModel}
              onChange={(e) => setAgentModel(e.target.value)}
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="submit"
              disabled={busy}
              className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              Save Anthropic settings
            </button>
            {status?.anthropic && (
              <button
                type="button"
                onClick={clearAnthropicKey}
                disabled={busy}
                className="rounded-md border border-red-300 px-4 py-2 text-sm text-red-700 hover:bg-red-50 disabled:opacity-50"
              >
                Clear key
              </button>
            )}
          </div>
        </form>
      </section>

      <section className="mt-8 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Email notifications (SMTP)
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          Optional. When set, the platform emails you the moment an agent
          queues a tier-2/3 approval. Best-effort: SMTP failures never
          block the agent loop.
        </p>
        <form className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2" onSubmit={saveSmtp}>
          <div>
            <label className="block text-xs text-slate-500">SMTP host</label>
            <input
              className={`mt-1 ${INPUT_CLS}`}
              placeholder="smtp.gmail.com"
              value={smtpHost}
              onChange={(e) => setSmtpHost(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500">Port</label>
            <input
              type="number"
              min={1}
              max={65535}
              className={`mt-1 ${INPUT_CLS}`}
              placeholder="587"
              value={smtpPort}
              onChange={(e) => setSmtpPort(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500">Username</label>
            <input
              className={`mt-1 ${INPUT_CLS}`}
              value={smtpUser}
              onChange={(e) => setSmtpUser(e.target.value)}
              autoComplete="username"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500">
              Password
              {status?.smtp_password_set && (
                <span className="ml-2 rounded bg-green-100 px-1.5 py-0.5 text-xs font-semibold text-green-800">
                  set
                </span>
              )}
            </label>
            <input
              type="password"
              autoComplete="off"
              className={`mt-1 ${INPUT_CLS}`}
              placeholder={
                status?.smtp_password_set ? "•••••••• (leave blank to keep)" : ""
              }
              value={smtpPassword}
              onChange={(e) => setSmtpPassword(e.target.value)}
            />
          </div>
          <div className="sm:col-span-2">
            <label className="block text-xs text-slate-500">From address</label>
            <input
              type="email"
              className={`mt-1 ${INPUT_CLS}`}
              placeholder="ops@your-company.com"
              value={smtpFrom}
              onChange={(e) => setSmtpFrom(e.target.value)}
            />
          </div>
          <div className="sm:col-span-2">
            <label className="block text-xs text-slate-500">
              Public app URL (for email links)
            </label>
            <input
              className={`mt-1 ${INPUT_CLS}`}
              placeholder="https://switchboard.your-company.com"
              value={appBaseUrl}
              onChange={(e) => setAppBaseUrl(e.target.value)}
            />
          </div>
          <label className="flex items-center gap-2 sm:col-span-2">
            <input
              type="checkbox"
              checked={smtpUseTls}
              onChange={(e) => setSmtpUseTls(e.target.checked)}
            />
            <span className="text-sm text-slate-700">Use STARTTLS</span>
          </label>
          <div className="flex flex-wrap items-end gap-2 sm:col-span-2">
            <button
              type="submit"
              disabled={busy}
              className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              Save SMTP settings
            </button>
            <div className="flex flex-1 items-center gap-2">
              <input
                type="email"
                className="flex-1 rounded-md border border-slate-300 px-3 py-1.5 text-sm"
                placeholder="test recipient"
                value={testTo}
                onChange={(e) => setTestTo(e.target.value)}
              />
              <button
                type="button"
                onClick={sendTestEmail}
                disabled={busy || !status?.smtp_configured || !testTo}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50"
              >
                Send test email
              </button>
            </div>
          </div>
        </form>
      </section>

      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Profile
          </h2>
          <form className="mt-3 space-y-3" onSubmit={saveProfile}>
            <div>
              <label className="block text-xs text-slate-500">Email</label>
              <input
                value={me?.email ?? ""}
                disabled
                className="mt-1 w-full rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-500"
              />
              <p className="mt-1 text-xs text-slate-400">
                Email is your login; change is not yet supported.
              </p>
            </div>
            <div>
              <label className="block text-xs text-slate-500">Name</label>
              <input
                className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </div>
            <button
              type="submit"
              disabled={busy || !name.trim() || name === me?.name}
              className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              Save profile
            </button>
          </form>
        </section>

        <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Change password
          </h2>
          <form className="mt-3 space-y-3" onSubmit={changePassword}>
            <div>
              <label className="block text-xs text-slate-500">
                Current password
              </label>
              <input
                type="password"
                autoComplete="current-password"
                className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
                required
              />
            </div>
            <div>
              <label className="block text-xs text-slate-500">
                New password (min. 8 chars)
              </label>
              <input
                type="password"
                autoComplete="new-password"
                className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                value={next}
                onChange={(e) => setNext(e.target.value)}
                required
              />
            </div>
            <div>
              <label className="block text-xs text-slate-500">
                Confirm new password
              </label>
              <input
                type="password"
                autoComplete="new-password"
                className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                required
              />
            </div>
            <button
              type="submit"
              disabled={busy || !current || !next || !confirm}
              className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              Change password
            </button>
          </form>
        </section>
      </div>
    </AppShell>
  );
}
