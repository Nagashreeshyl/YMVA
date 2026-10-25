"use client";

import clsx from "clsx";
import { useState } from "react";
import { PageHeader } from "@/components/ui";
import { useMe } from "@/lib/session";
import { AccountSettings } from "./account";
import { AuditLogList } from "./audit-log";
import { TeamSettings } from "./team";

type Tab = "team" | "audit" | "account";

export default function SettingsPage() {
  const { data: me } = useMe();
  const isAdmin = me?.role === "admin";
  const tabs: { id: Tab; label: string }[] = [
    ...(isAdmin ? [{ id: "team" as const, label: "Team" }] : []),
    { id: "audit", label: "Audit log" },
    { id: "account", label: "Your account" },
  ];
  const [selected, setSelected] = useState<Tab | null>(null);
  const tab = selected ?? (isAdmin ? "team" : "audit");

  return (
    <>
      <PageHeader title="Settings" description="People, history of changes, and your sign-in." />
      <div
        role="tablist"
        aria-label="Settings sections"
        className="mb-6 inline-flex rounded-full bg-surface p-1"
      >
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`panel-${t.id}`}
            onClick={() => setSelected(t.id)}
            className={clsx(
              "h-8 rounded-full px-4 text-[13px] font-medium transition-colors duration-150",
              tab === t.id
                ? "bg-paper text-ink shadow-[0_1px_2px_rgb(0_0_0/0.08)]"
                : "text-muted hover:text-ink",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === "team" && isAdmin && <TeamSettings currentUserId={me.id} />}
        {tab === "audit" && <AuditLogList />}
        {tab === "account" && <AccountSettings />}
      </div>
    </>
  );
}
