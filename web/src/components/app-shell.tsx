"use client";

import clsx from "clsx";
import {
  AudioLines,
  Bot,
  Gauge,
  LogOut,
  Megaphone,
  Menu,
  Phone,
  PhoneCall,
  Plug,
  Settings,
  Wrench,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { canOpen, ROLE_LABELS } from "@/lib/permissions";
import { useMe } from "@/lib/session";
import { Skeleton } from "./ui";

const NAV = [
  { href: "/", label: "Overview", icon: Gauge },
  { href: "/agents", label: "Agents", icon: Bot },
  { href: "/calls", label: "Calls", icon: PhoneCall },
  { href: "/campaigns", label: "Campaigns", icon: Megaphone },
  { href: "/tools", label: "Tools", icon: Wrench },
  { href: "/numbers", label: "Numbers", icon: Phone },
  { href: "/integrations", label: "Integrations", icon: Plug },
  { href: "/settings", label: "Settings", icon: Settings },
];

function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

function Wordmark() {
  return (
    <Link href="/" className="flex items-center gap-2.5 rounded-lg px-2 py-1">
      <span className="grid size-8 place-items-center rounded-lg bg-[#0a0a0a] text-gold ring-1 ring-line">
        <AudioLines className="size-[18px]" strokeWidth={2} aria-hidden />
      </span>
      <span className="flex flex-col leading-tight">
        <span className="text-[13px] font-bold tracking-[0.06em]">YELLOW METAL</span>
        <span className="text-2xs text-muted">Voice Agent</span>
      </span>
    </Link>
  );
}

function NavList({ pathname, onNavigate }: { pathname: string | null; onNavigate?: () => void }) {
  const { data: me } = useMe();
  return (
    <nav aria-label="Main" className="flex flex-col gap-0.5">
      {NAV.filter(({ href }) => me && canOpen(me, href)).map(({ href, label, icon: Icon }) => {
        const active = pathname !== null && isActive(pathname, href);
        return (
          <Link
            key={href}
            href={href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={clsx(
              "group flex h-9 items-center gap-3 rounded-full px-3 text-sm transition-colors duration-150",
              active
                ? "bg-paper font-medium text-ink shadow-[0_1px_2px_rgb(0_0_0/0.06)]"
                : "text-muted hover:bg-surface-hover hover:text-ink",
            )}
          >
            <Icon
              className={clsx("size-[17px]", active ? "text-ink" : "text-faint group-hover:text-ink")}
              strokeWidth={1.75}
              aria-hidden
            />
            {label}
            {active && <span className="ml-auto size-1.5 rounded-full bg-gold" aria-hidden />}
          </Link>
        );
      })}
    </nav>
  );
}

function CurrentNav({ onNavigate }: { onNavigate?: () => void }) {
  return <NavList pathname={usePathname()} onNavigate={onNavigate} />;
}

// The current path is request-time data; the fallback renders the same menu without highlighting.
function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <Suspense fallback={<NavList pathname={null} onNavigate={onNavigate} />}>
      <CurrentNav onNavigate={onNavigate} />
    </Suspense>
  );
}

function UserFooter() {
  const { data: me, isPending } = useMe();
  const [signingOut, setSigningOut] = useState(false);

  async function signOut() {
    setSigningOut(true);
    await api("/auth/logout", { method: "POST" }).catch(() => {});
    // Full reload on purpose: drops every cached query from the old session.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign("/login");
  }

  if (isPending || !me) {
    return (
      <div className="flex items-center gap-3 px-2">
        <Skeleton className="size-8 rounded-full" />
        <Skeleton className="h-4 w-28" />
      </div>
    );
  }
  return (
    <div className="flex items-center gap-3 px-2">
      <span className="grid size-8 shrink-0 place-items-center rounded-full bg-surface-hover text-[13px] font-semibold">
        {me.name.charAt(0).toUpperCase()}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] font-medium">{me.name}</p>
        <p className="truncate text-2xs text-muted">{ROLE_LABELS[me.role]}</p>
      </div>
      <button
        type="button"
        onClick={signOut}
        disabled={signingOut}
        aria-label="Sign out"
        title="Sign out"
        className="grid size-8 place-items-center rounded-full text-muted transition-colors hover:bg-surface-hover hover:text-ink disabled:opacity-50"
      >
        <LogOut className="size-4" strokeWidth={1.75} />
      </button>
    </div>
  );
}

// Pages the role can't use are never rendered (the server refuses their data too).
function Guarded({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { data: me, isPending } = useMe();
  if (isPending) return <Skeleton className="h-96 rounded-xl" />;
  if (me && !canOpen(me, pathname)) {
    return (
      <div className="flex flex-col items-center gap-3 py-24 text-center">
        <h1 className="font-display text-[28px] font-extrabold tracking-[-0.02em]">No access</h1>
        <p className="max-w-sm text-muted">
          Your role ({ROLE_LABELS[me.role]}) can&apos;t open this page. Ask an admin if you need it.
        </p>
        <Link
          href="/"
          className="mt-2 inline-flex h-10 items-center rounded-full bg-ink px-5 text-sm font-medium text-on-ink"
        >
          Back to Overview
        </Link>
      </div>
    );
  }
  return children;
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[248px_1fr]">
      <aside className="sticky top-0 hidden h-dvh flex-col gap-6 border-r border-line bg-rail px-3 py-5 lg:flex">
        <Wordmark />
        <NavLinks />
        <div className="mt-auto">
          <UserFooter />
        </div>
      </aside>

      <div className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-line bg-paper/90 px-4 backdrop-blur lg:hidden">
        <Wordmark />
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label="Open menu"
          aria-expanded={open}
          className="grid size-10 place-items-center rounded-full hover:bg-surface"
        >
          <Menu className="size-5" strokeWidth={1.75} />
        </button>
      </div>

      {open && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Menu">
          <button
            type="button"
            aria-label="Close menu"
            className="absolute inset-0 bg-black/30"
            onClick={() => setOpen(false)}
          />
          <div className="absolute inset-y-0 left-0 flex w-[280px] max-w-[85vw] flex-col gap-6 bg-rail px-3 py-4 shadow-float">
            <div className="flex items-center justify-between">
              <Wordmark />
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Close menu"
                className="grid size-10 place-items-center rounded-full hover:bg-surface-hover"
              >
                <X className="size-5" strokeWidth={1.75} />
              </button>
            </div>
            <NavLinks onNavigate={() => setOpen(false)} />
            <div className="mt-auto">
              <UserFooter />
            </div>
          </div>
        </div>
      )}

      <main className="mx-auto w-full max-w-[1200px] px-4 py-8 sm:px-8 lg:py-10">
        <Suspense fallback={<Skeleton className="h-96 rounded-xl" />}>
          <Guarded>{children}</Guarded>
        </Suspense>
      </main>
    </div>
  );
}
