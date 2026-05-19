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
  smtp: boolean;
  smtp_host: string | null;
  smtp_from: string | null;
  app_base_url: string | null;
  documents_dir: string;
  portal_integration_backend: string;
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

export default function SettingsPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [name, setName] = useState("");
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const [u, s] = await Promise.all([
          apiFetch<Me>("/auth/me"),
          apiFetch<SystemStatus>("/system/status"),
        ]);
        setMe(u);
        setName(u.name);
        setStatus(s);
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
          <p className="mt-1 text-xs text-slate-500">
            What's configured. Set values via the deploy environment
            (.env or your hosting platform); the platform never echoes
            secrets back.
          </p>
          <dl className="mt-3 grid grid-cols-1 gap-y-2 text-sm sm:grid-cols-2">
            <ReadyRow
              label="Anthropic API key"
              ok={status.anthropic}
              detail={status.anthropic ? `model: ${status.agent_model}` : "agent runs return 503 until set"}
            />
            <ReadyRow
              label="Email notifications"
              ok={status.smtp}
              detail={
                status.smtp
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
          </dl>
        </section>
      )}

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
