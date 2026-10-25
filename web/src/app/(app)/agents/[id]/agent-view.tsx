"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { AgentStatusBadge } from "@/components/agent-status";
import { Button, ErrorNotice, Segmented, Skeleton } from "@/components/ui";
import { api, type AgentDetail, type AgentOptions } from "@/lib/api";
import { useMe } from "@/lib/session";
import { CallPhone } from "./call-phone";
import { AgentEditor } from "./editor";
import { TestCall } from "./test-call";
import { Versions } from "./versions";

type Tab = "configure" | "test" | "call" | "versions";

export function AgentView() {
  const { id } = useParams<{ id: string }>();
  const { data: me } = useMe();
  const canEdit = me?.role === "admin";
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("configure");
  const agent = useQuery({ queryKey: ["agent", id], queryFn: () => api<AgentDetail>(`/agents/${id}`) });
  const options = useQuery({
    queryKey: ["agent-options"],
    queryFn: () => api<AgentOptions>("/agents/options"),
  });

  const publishLatest = useMutation({
    mutationFn: () =>
      api<AgentDetail>(`/agents/${id}/publish`, {
        method: "POST",
        json: { version_id: agent.data?.latest_version_id },
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["agent", id], updated);
      queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
  });
  const setStatus = useMutation({
    mutationFn: (status: "active" | "paused") =>
      api<AgentDetail>(`/agents/${id}`, { method: "PATCH", json: { status } }),
    onSuccess: (updated) => queryClient.setQueryData(["agent", id], updated),
  });

  if (agent.isPending || options.isPending) {
    return (
      <div className="flex flex-col gap-6">
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-96 rounded-xl" />
      </div>
    );
  }
  if (agent.isError || options.isError) {
    return <ErrorNotice error={agent.error ?? options.error} onRetry={() => agent.refetch()} />;
  }
  const a = agent.data;

  return (
    <>
      <Link
        href="/agents"
        className="mb-4 inline-flex items-center gap-1.5 text-[13px] text-muted hover:text-ink"
      >
        <ArrowLeft className="size-4" aria-hidden /> Agents
      </Link>
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="font-display text-[28px] leading-9 font-extrabold tracking-[-0.02em]">{a.name}</h1>
            <AgentStatusBadge agent={a} />
          </div>
          <p className="mt-1 text-muted">
            {a.published_version === null
              ? `Draft · v${a.latest_version} not published yet`
              : a.has_unpublished_changes
                ? `v${a.published_version} is live · v${a.latest_version} has unpublished changes`
                : `v${a.published_version} is live`}
          </p>
        </div>
        {canEdit && (
          <div className="flex flex-wrap gap-2">
            {a.status === "active" && (
              <Button
                variant="secondary"
                loading={setStatus.isPending}
                onClick={() => setStatus.mutate("paused")}
              >
                Pause
              </Button>
            )}
            {a.status === "paused" && (
              <Button
                variant="secondary"
                loading={setStatus.isPending}
                onClick={() => setStatus.mutate("active")}
              >
                Resume
              </Button>
            )}
            {a.has_unpublished_changes && (
              <Button loading={publishLatest.isPending} onClick={() => publishLatest.mutate()}>
                Publish v{a.latest_version}
              </Button>
            )}
          </div>
        )}
      </header>
      {(publishLatest.isError || setStatus.isError) && (
        <div className="mb-4">
          <ErrorNotice error={publishLatest.error ?? setStatus.error} />
        </div>
      )}
      <div className="mb-6">
        <Segmented
          label="Agent sections"
          value={tab}
          onChange={setTab}
          options={[
            { value: "configure", label: "Configure" },
            ...(canEdit
              ? [
                  { value: "test" as const, label: "Test in browser" },
                  { value: "call" as const, label: "Call a phone" },
                ]
              : []),
            { value: "versions", label: `Versions (${a.versions.length})` },
          ]}
        />
      </div>
      {tab === "configure" && (
        <AgentEditor key={a.latest_version_id} agent={a} options={options.data} readOnly={!canEdit} />
      )}
      {tab === "test" && canEdit && <TestCall agent={a} />}
      {tab === "call" && canEdit && <CallPhone agent={a} />}
      {tab === "versions" && <Versions agent={a} canEdit={canEdit} />}
    </>
  );
}
