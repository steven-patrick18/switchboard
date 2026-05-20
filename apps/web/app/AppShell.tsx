"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { apiFetch, clearToken, getToken } from "@/lib/api";

const LINKS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/workspaces", label: "Workspaces" },
  { href: "/approvals", label: "Approval queue" },
  { href: "/history", label: "History" },
  { href: "/agents", label: "AI Agents" },
  { href: "/settings", label: "Settings" },
];

type Me = { id: string; email: string; name: string };

export default function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);
  const [pending, setPending] = useState<number | null>(null);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    (async () => {
      try {
        setMe(await apiFetch<Me>("/auth/me"));
      } catch {
        clearToken();
        router.replace("/login");
        return;
      }
      setReady(true);
    })();
  }, [router]);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    async function tick() {
      try {
        const r = await apiFetch<{ pending: number }>("/approvals/count");
        if (!cancelled) setPending(r.pending);
      } catch {
        // Soft-fail: silence transient network blips so the badge
        // doesn't whip-saw on a fast page change.
      }
    }
    tick();
    const id = setInterval(tick, 30000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [ready, pathname]);

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 text-sm text-slate-500">
        Loading…
      </div>
    );
  }

  function signOut() {
    clearToken();
    router.replace("/login");
  }

  return (
    <div className="flex min-h-screen bg-slate-50">
      <aside className="fixed inset-y-0 left-0 hidden w-60 flex-col border-r border-slate-800 bg-slate-900 text-slate-100 lg:flex">
        <div className="px-5 pb-4 pt-6">
          <div className="text-lg font-semibold tracking-tight">Switchboard</div>
          <div className="mt-0.5 text-xs text-slate-400">
            AI Operator Platform
          </div>
        </div>
        <nav className="flex-1 px-2">
          <ul className="space-y-1">
            {LINKS.map((l) => {
              const active = pathname === l.href || pathname.startsWith(l.href + "/");
              const showBadge =
                l.href === "/approvals" && pending !== null && pending > 0;
              return (
                <li key={l.href}>
                  <Link
                    href={l.href}
                    className={
                      "flex items-center justify-between rounded-md px-3 py-2 text-sm transition " +
                      (active
                        ? "bg-slate-800 text-white"
                        : "text-slate-300 hover:bg-slate-800 hover:text-white")
                    }
                  >
                    <span>{l.label}</span>
                    {showBadge && (
                      <span className="rounded-full bg-amber-400 px-2 py-0.5 text-xs font-semibold text-slate-900">
                        {pending}
                      </span>
                    )}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        <div className="border-t border-slate-800 px-5 py-4">
          <div className="truncate text-xs text-slate-400">{me?.email}</div>
          <button
            onClick={signOut}
            className="mt-2 text-xs text-slate-300 underline-offset-2 hover:text-white hover:underline"
          >
            Sign out
          </button>
        </div>
      </aside>

      {/* Compact top bar on small screens */}
      <header className="fixed inset-x-0 top-0 z-10 flex items-center justify-between border-b border-slate-800 bg-slate-900 px-4 py-2 text-sm text-slate-100 lg:hidden">
        <span className="font-semibold">Switchboard</span>
        <nav className="flex gap-3">
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className={
                pathname === l.href ? "text-white" : "text-slate-300"
              }
            >
              {l.label.split(" ")[0]}
            </Link>
          ))}
          <button onClick={signOut} className="text-slate-300">
            Out
          </button>
        </nav>
      </header>

      <div className="flex-1 pt-14 lg:ml-60 lg:pt-0">
        <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-10 lg:py-10">
          {children}
        </main>
      </div>
    </div>
  );
}
