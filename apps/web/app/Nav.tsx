"use client";

import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";

import { clearToken } from "@/lib/api";

const LINKS = [
  { href: "/workspaces", label: "Workspaces" },
  { href: "/approvals", label: "Approval queue" },
];

export default function Nav() {
  const router = useRouter();
  const pathname = usePathname();

  function signOut() {
    clearToken();
    router.replace("/login");
  }

  return (
    <nav className="mb-8 flex items-center justify-between border-b border-slate-200 pb-4">
      <div className="flex gap-6">
        {LINKS.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className={
              pathname === l.href
                ? "text-sm font-semibold text-slate-900"
                : "text-sm text-slate-500 hover:text-slate-900"
            }
          >
            {l.label}
          </Link>
        ))}
      </div>
      <button onClick={signOut} className="text-sm text-slate-500 underline">
        Sign out
      </button>
    </nav>
  );
}
