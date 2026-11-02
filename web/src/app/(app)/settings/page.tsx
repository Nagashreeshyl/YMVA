"use client";

import clsx from "clsx";
import { useState } from "react";
import { PageHeader } from "@/components/ui";
import { can } from "@/lib/permissions";
import { useMe } from "@/lib/session";
import { AccountSettings } from "./account";
import { ApiKeySettings } from "./api-keys";
import { AuditLogList } from "./audit-log";
import { DoNotCallSettings } from "./do-not-call";
import { HealthSettings } from "./health";
import { TeamSettings } from "./team";

type Tab = "team" | "keys" | "health" | "dnc" | "audit" | "account";

export default function SettingsPage() {
  const { data: me } = useMe();
  const allowed: Record<Tab, boolean> = {
    team: can(me, "users.manage"),
    keys: can(me, "users.manage"),
    health: can(me, "settings.manage"),
    dnc: can(me, "dnc.manage"),
    audit: can(me, "audit.view"),
    account: true,
  };
  const tabs = (
    [
      { id: "team", label: "Team" },
      { id: "keys", label: "API keys" },
      { id: "health", label: "Health & alerts" },
      { id: "dnc", label: "Do not call" },
      { id: "audit", label: "Audit log" },
      { id: "account", label: "Your account" },
    ] as const
  ).filter((t) => allowed[t.id]);
  const [selected, setSelected] = useState<Tab | null>(null);
  const tab = selected ?? tabs[0].id;

  return (
    <>
      <PageHeader
        title="Settings"
        description="People, system health and alerts, numbers never to call, history of changes, and your sign-in."
      />
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
        {tab === "team" && allowed.team && me && <TeamSettings currentUserId={me.id} />}
        {tab === "keys" && allowed.keys && <ApiKeySettings />}
        {tab === "health" && allowed.health && <HealthSettings canEdit />}
        {tab === "dnc" && allowed.dnc && <DoNotCallSettings canEdit />}
        {tab === "audit" && allowed.audit && <AuditLogList />}
        {tab === "account" && <AccountSettings />}
      </div>
    </>
  );
}
