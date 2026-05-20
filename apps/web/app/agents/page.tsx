"use client";

import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import AppShell from "@/app/AppShell";

type ToolCatalogEntry = {
  name: string;
  description: string;
  tier: string;
};

type Agent = {
  id: string | null;
  name: string;
  description: string | null;
  system_prompt: string;
  tool_names: string[];
  model: string | null;
  effort: string | null;
  enabled: boolean;
  is_builtin: boolean;
  created_at: string | null;
  updated_at: string | null;
};

const TIER_STYLE: Record<string, string> = {
  T0: "bg-green-100 text-green-800",
  T1: "bg-emerald-100 text-emerald-800",
  T2: "bg-amber-100 text-amber-800",
  T3: "bg-red-100 text-red-800",
};

const TIER_LABEL: Record<string, string> = {
  T0: "auto",
  T1: "auto + notify",
  T2: "needs approval",
  T3: "high-risk (needs approval)",
};

const EMPTY_DRAFT: AgentDraft = {
  id: null,
  name: "",
  description: "",
  system_prompt: "",
  tool_names: [],
  model: "",
  effort: "",
  enabled: true,
};

type AgentDraft = {
  id: string | null;
  name: string;
  description: string;
  system_prompt: string;
  tool_names: string[];
  model: string;
  effort: string;
  enabled: boolean;
};

const INPUT_CLS =
  "w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-slate-500 focus:outline-none";

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [tools, setTools] = useState<ToolCatalogEntry[]>([]);
  const [draft, setDraft] = useState<AgentDraft | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [a, t] = await Promise.all([
        apiFetch<Agent[]>("/agents"),
        apiFetch<ToolCatalogEntry[]>("/agents/tools"),
      ]);
      setAgents(a);
      setTools(t);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function startNew() {
    setDraft({ ...EMPTY_DRAFT });
    setMsg(null);
    setErr(null);
  }

  function startEdit(a: Agent) {
    if (a.is_builtin) {
      // Clone the built-in into a draft so the operator can save a
      // customized override.
      setDraft({
        id: null,
        name: a.name,
        description: a.description ?? "",
        system_prompt: a.system_prompt,
        tool_names: [...a.tool_names],
        model: a.model ?? "",
        effort: a.effort ?? "",
        enabled: true,
      });
      setMsg(
        `Cloning built-in ${a.name} — save to create a custom override that takes precedence at run time.`,
      );
    } else {
      setDraft({
        id: a.id,
        name: a.name,
        description: a.description ?? "",
        system_prompt: a.system_prompt,
        tool_names: [...a.tool_names],
        model: a.model ?? "",
        effort: a.effort ?? "",
        enabled: a.enabled,
      });
    }
    setErr(null);
  }

  function toggleTool(name: string) {
    setDraft((d) =>
      d == null
        ? d
        : {
            ...d,
            tool_names: d.tool_names.includes(name)
              ? d.tool_names.filter((n) => n !== name)
              : [...d.tool_names, name],
          },
    );
  }

  async function save() {
    if (!draft) return;
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const payload = {
        name: draft.name.trim(),
        description: draft.description.trim() || null,
        system_prompt: draft.system_prompt,
        tool_names: draft.tool_names,
        model: draft.model.trim() || null,
        effort: draft.effort.trim() || null,
        enabled: draft.enabled,
      };
      if (draft.id) {
        // Update — strip name and only send editable fields.
        const { name: _omit_name, ...update } = payload;
        await apiFetch(`/agents/${draft.id}`, {
          method: "PUT",
          body: JSON.stringify(update),
        });
        setMsg("Agent updated.");
      } else {
        await apiFetch("/agents", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        setMsg("Agent saved.");
      }
      setDraft(null);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  async function remove(a: Agent) {
    if (!a.id) return;
    if (!window.confirm(`Delete the custom agent "${a.name}"?`)) return;
    setBusy(true);
    try {
      await apiFetch(`/agents/${a.id}`, { method: "DELETE" });
      setMsg(`Deleted ${a.name}.`);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setBusy(false);
    }
  }

  const sortedTools = [...tools].sort((a, b) =>
    a.tier === b.tier ? a.name.localeCompare(b.name) : a.tier.localeCompare(b.tier),
  );

  return (
    <AppShell>
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold text-slate-900">AI Agents</h1>
        <button
          onClick={startNew}
          disabled={busy}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
        >
          + New agent
        </button>
      </div>
      <p className="mt-1 text-sm text-slate-600">
        Compose an agent: write its system prompt, pick which tools it can
        use, and (optionally) override the default model. Tools stay
        code-defined for safety — their tier classification decides which
        actions need your approval.
      </p>

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

      <ul className="mt-6 space-y-3">
        {agents.map((a) => (
          <li
            key={(a.id ?? "builtin") + a.name}
            className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-base font-semibold text-slate-900">
                    {a.name}
                  </span>
                  {a.is_builtin ? (
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs font-semibold text-slate-700">
                      built-in
                    </span>
                  ) : (
                    <span className="rounded bg-indigo-100 px-1.5 py-0.5 text-xs font-semibold text-indigo-800">
                      custom
                    </span>
                  )}
                  {!a.enabled && (
                    <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-semibold text-amber-800">
                      disabled
                    </span>
                  )}
                  {a.model && (
                    <span className="text-xs text-slate-500">model: {a.model}</span>
                  )}
                </div>
                {a.description && (
                  <p className="mt-1 text-sm text-slate-600">{a.description}</p>
                )}
                <p className="mt-1 text-xs text-slate-500">
                  {a.tool_names.length} tool{a.tool_names.length === 1 ? "" : "s"}:{" "}
                  <span className="font-mono">
                    {a.tool_names.join(", ") || "(none)"}
                  </span>
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => startEdit(a)}
                  className="rounded-md border border-slate-300 px-3 py-1 text-xs hover:bg-slate-50"
                >
                  {a.is_builtin ? "Clone & customize" : "Edit"}
                </button>
                {!a.is_builtin && (
                  <button
                    onClick={() => remove(a)}
                    disabled={busy}
                    className="rounded-md border border-red-300 px-3 py-1 text-xs text-red-700 hover:bg-red-50 disabled:opacity-50"
                  >
                    Delete
                  </button>
                )}
              </div>
            </div>
          </li>
        ))}
      </ul>

      {draft && (
        <div
          className="fixed inset-0 z-20 flex items-center justify-center bg-slate-900/60 p-4"
          onClick={() => setDraft(null)}
        >
          <div
            className="flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-lg bg-white shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
              <h2 className="text-base font-semibold text-slate-900">
                {draft.id ? `Edit agent: ${draft.name}` : "New agent"}
              </h2>
              <button
                onClick={() => setDraft(null)}
                className="rounded-md px-2 py-1 text-sm text-slate-600 hover:bg-slate-100"
              >
                close ✕
              </button>
            </div>
            <div className="space-y-4 overflow-auto p-5">
              <div>
                <label className="block text-xs text-slate-500">
                  Name <span className="text-red-600">*</span>
                </label>
                <input
                  className={`mt-1 ${INPUT_CLS}`}
                  placeholder="e.g. carrier, billing, intake-helper"
                  value={draft.name}
                  disabled={!!draft.id}
                  onChange={(e) =>
                    setDraft({ ...draft, name: e.target.value.toLowerCase() })
                  }
                  pattern="[a-z0-9_]+"
                />
                <p className="mt-1 text-xs text-slate-400">
                  Lowercase letters / numbers / underscores. Cannot change after save.
                </p>
              </div>
              <div>
                <label className="block text-xs text-slate-500">Description</label>
                <input
                  className={`mt-1 ${INPUT_CLS}`}
                  placeholder="Short description shown in the agent list"
                  value={draft.description}
                  onChange={(e) =>
                    setDraft({ ...draft, description: e.target.value })
                  }
                />
              </div>
              <div>
                <label className="block text-xs text-slate-500">
                  System prompt <span className="text-red-600">*</span>
                </label>
                <textarea
                  className={`mt-1 h-48 ${INPUT_CLS} font-mono text-xs`}
                  placeholder="You are the X agent. Your scope is Y..."
                  value={draft.system_prompt}
                  onChange={(e) =>
                    setDraft({ ...draft, system_prompt: e.target.value })
                  }
                />
                <p className="mt-1 text-xs text-slate-400">
                  Tell the agent who it is, what scope it owns, and any
                  operating rules. Min. 20 characters.
                </p>
              </div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label className="block text-xs text-slate-500">
                    Model (leave blank for default)
                  </label>
                  <input
                    className={`mt-1 ${INPUT_CLS}`}
                    placeholder="claude-opus-4-7"
                    value={draft.model}
                    onChange={(e) => setDraft({ ...draft, model: e.target.value })}
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-500">Effort</label>
                  <select
                    className={`mt-1 ${INPUT_CLS}`}
                    value={draft.effort}
                    onChange={(e) =>
                      setDraft({ ...draft, effort: e.target.value })
                    }
                  >
                    <option value="">(default)</option>
                    <option value="low">low</option>
                    <option value="medium">medium</option>
                    <option value="high">high</option>
                    <option value="xhigh">xhigh</option>
                    <option value="max">max</option>
                  </select>
                </div>
              </div>
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={draft.enabled}
                  onChange={(e) =>
                    setDraft({ ...draft, enabled: e.target.checked })
                  }
                />
                Enabled
                <span className="text-xs text-slate-400">
                  (disable to fall back to the built-in of the same name without deleting)
                </span>
              </label>
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Tools this agent can use
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  Check the box to include the tool. T0/T1 run inline; T2/T3
                  always queue an approval — pick those carefully.
                </p>
                <ul className="mt-2 max-h-64 overflow-auto divide-y divide-slate-200 rounded-md border border-slate-200">
                  {sortedTools.map((t) => {
                    const checked = draft.tool_names.includes(t.name);
                    return (
                      <li
                        key={t.name}
                        className="flex items-start gap-3 p-2 text-sm hover:bg-slate-50"
                      >
                        <input
                          type="checkbox"
                          className="mt-0.5"
                          checked={checked}
                          onChange={() => toggleTool(t.name)}
                        />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-sm font-medium text-slate-900">
                              {t.name}
                            </span>
                            <span
                              className={
                                "rounded px-1.5 py-0.5 text-xs font-semibold " +
                                (TIER_STYLE[t.tier] ?? "bg-slate-100 text-slate-700")
                              }
                              title={TIER_LABEL[t.tier]}
                            >
                              {t.tier}
                            </span>
                          </div>
                          <p className="text-xs text-slate-500">{t.description}</p>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </div>
            </div>
            <div className="flex items-center justify-end gap-2 border-t border-slate-200 px-5 py-3">
              <button
                onClick={() => setDraft(null)}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50"
              >
                Cancel
              </button>
              <button
                onClick={save}
                disabled={
                  busy ||
                  !draft.name.trim() ||
                  draft.system_prompt.trim().length < 20
                }
                className="rounded-md bg-slate-900 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50"
              >
                {draft.id ? "Save changes" : "Create agent"}
              </button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
