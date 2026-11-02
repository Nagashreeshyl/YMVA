"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Megaphone, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  Badge,
  Button,
  EmptyState,
  ErrorNotice,
  PageHeader,
  Panel,
  Skeleton,
} from "@/components/ui";
import {
  api,
  type Campaign,
  type CampaignInput,
  type CampaignStatus,
} from "@/lib/api";
import { formatNumber, relativeTime } from "@/lib/format";
import { can } from "@/lib/permissions";
import { useMe } from "@/lib/session";
import { CampaignForm } from "./campaign-form";

const STATUS: Record<
  CampaignStatus,
  { label: string; tone: "neutral" | "success" | "warning" | "gold" }
> = {
  draft: { label: "Draft", tone: "neutral" },
  running: { label: "Running", tone: "success" },
  paused: { label: "Paused", tone: "warning" },
  completed: { label: "Completed", tone: "gold" },
};

export function CampaignStatusBadge({ status }: { status: CampaignStatus }) {
  return <Badge tone={STATUS[status].tone}>{STATUS[status].label}</Badge>;
}

/** Share of contacts finished (reached or given up on). */
export function progress(c: Campaign): number {
  return c.counts.total
    ? (c.counts.done + c.counts.failed) / c.counts.total
    : 0;
}

export function CampaignsView() {
  const { data: me } = useMe();
  const canEdit = can(me, "campaigns.manage");
  const router = useRouter();
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const campaigns = useQuery({
    queryKey: ["campaigns"],
    queryFn: () => api<Campaign[]>("/campaigns"),
    refetchInterval: (q) =>
      q.state.data?.some((c) => c.status === "running") ? 5000 : false,
  });
  const create = useMutation({
    mutationFn: (body: CampaignInput) =>
      api<Campaign>("/campaigns", { method: "POST", json: body }),
    onSuccess: (campaign) => {
      queryClient.invalidateQueries({ queryKey: ["campaigns"] });
      router.push(`/campaigns/${campaign.id}`);
    },
  });

  return (
    <>
      <PageHeader
        title="Campaigns"
        description="An agent calls a list of customers within calling hours, retries those who don't answer, and records each result."
        actions={
          canEdit && !creating ? (
            <Button onClick={() => setCreating(true)}>
              <Plus className="size-4" aria-hidden /> New campaign
            </Button>
          ) : undefined
        }
      />
      <div className="flex flex-col gap-6">
        {creating && (
          <Panel
            title="New campaign"
            description="You add the contacts on the next screen."
          >
            <CampaignForm
              initial={{}}
              submitLabel="Create campaign"
              pending={create.isPending}
              error={create.error}
              onSubmit={(body) => create.mutate(body)}
              onCancel={() => setCreating(false)}
            />
          </Panel>
        )}
        {campaigns.isPending ? (
          <Skeleton className="h-40 rounded-xl" />
        ) : campaigns.isError ? (
          <ErrorNotice
            error={campaigns.error}
            onRetry={() => campaigns.refetch()}
          />
        ) : campaigns.data.length === 0 ? (
          !creating && (
            <EmptyState
              icon={Megaphone}
              title="No campaigns yet"
              action={
                canEdit ? (
                  <Button onClick={() => setCreating(true)}>
                    Create your first campaign
                  </Button>
                ) : undefined
              }
            >
              Pick an agent and the numbers to call from, then import your
              customer list from a CSV or a Google Sheet.
            </EmptyState>
          )
        ) : (
          <ul className="divide-y divide-line rounded-xl border border-line">
            {campaigns.data.map((c) => (
              <li key={c.id}>
                <Link
                  href={`/campaigns/${c.id}`}
                  className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-4 transition-colors hover:bg-rail"
                >
                  <div className="min-w-0 flex-1">
                    <p className="font-medium">{c.name}</p>
                    <p className="text-[13px] text-muted">
                      {c.agent_name ?? "Agent deleted"} ·{" "}
                      {formatNumber(c.counts.total)} contacts ·{" "}
                      {formatNumber(c.counts.done)} reached
                    </p>
                  </div>
                  <div className="flex w-40 flex-col gap-1">
                    <div className="h-1.5 rounded-full bg-surface" aria-hidden>
                      <div
                        className="h-full rounded-full bg-chart-1"
                        style={{ width: `${Math.round(progress(c) * 100)}%` }}
                      />
                    </div>
                    <span className="text-2xs text-muted">
                      {Math.round(progress(c) * 100)}% finished ·{" "}
                      {relativeTime(c.created_at).toLowerCase()}
                    </span>
                  </div>
                  <CampaignStatusBadge status={c.status} />
                  <ChevronRight className="size-4 text-faint" aria-hidden />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
