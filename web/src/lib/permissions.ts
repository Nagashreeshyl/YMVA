import type { Role, User } from "@/lib/api";

/** What a role may do; the server sends the signed-in user's list (backend permissions.py). */
export type Perm =
  | "agents.view"
  | "agents.edit"
  | "calls.place"
  | "calls.view_all"
  | "calls.edit"
  | "recordings.listen"
  | "recordings.download"
  | "costs.view"
  | "campaigns.view"
  | "campaigns.manage"
  | "numbers.view"
  | "numbers.manage"
  | "tools.manage"
  | "integrations.manage"
  | "settings.manage"
  | "dnc.manage"
  | "users.manage"
  | "owners.manage"
  | "audit.view";

export const ROLE_LABELS: Record<Role, string> = {
  owner: "Owner",
  admin: "Admin",
  campaign_manager: "Campaign manager",
  caller: "Caller",
  reviewer: "Call reviewer",
  viewer: "Viewer",
};

export const ROLE_HINTS: Record<Role, string> = {
  owner: "Everything, including managing owners",
  admin: "Everything except managing owners",
  campaign_manager: "Runs campaigns and calls; can't change agents or settings",
  caller: "Places single calls and sees only their own calls",
  reviewer: "Call history, transcripts and recordings only",
  viewer: "Sees dashboards and results; can't change or start anything",
};

export function can(me: User | undefined, ...perms: Perm[]): boolean {
  return !!me && perms.some((p) => me.permissions.includes(p));
}

/** Pages and who may open them (any of the listed permissions; none listed = everyone). */
export const PAGES: { href: string; perms: Perm[] }[] = [
  { href: "/agents", perms: ["agents.view", "calls.place"] },
  { href: "/campaigns", perms: ["campaigns.view"] },
  { href: "/tools", perms: ["tools.manage"] },
  { href: "/numbers", perms: ["numbers.manage"] },
  { href: "/integrations", perms: ["integrations.manage"] },
];

export function canOpen(me: User | undefined, path: string): boolean {
  const page = PAGES.find(
    (p) => path === p.href || path.startsWith(`${p.href}/`),
  );
  return !page || can(me, ...page.perms);
}
