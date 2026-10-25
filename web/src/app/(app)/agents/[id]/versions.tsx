"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Badge, Button, ErrorNotice } from "@/components/ui";
import { api, type AgentDetail } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

export function Versions({ agent, canEdit }: { agent: AgentDetail; canEdit: boolean }) {
  const queryClient = useQueryClient();
  const publish = useMutation({
    mutationFn: (versionId: string) =>
      api<AgentDetail>(`/agents/${agent.id}/publish`, { method: "POST", json: { version_id: versionId } }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["agent", agent.id], updated);
      queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
  });

  return (
    <div className="flex flex-col gap-4">
      {publish.isError && <ErrorNotice error={publish.error} />}
      <ol className="divide-y divide-line rounded-xl border border-line">
        {agent.versions.map((v) => {
          const isLive = v.id === agent.published_version_id;
          return (
            <li key={v.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-4">
              <span className="tabular w-10 font-semibold">v{v.version}</span>
              <div className="min-w-0 flex-1">
                <p className={v.note ? "" : "text-muted"}>{v.note || "No description"}</p>
                <p className="text-2xs text-muted">
                  {formatDateTime(v.created_at)}
                  {v.created_by && ` · ${v.created_by}`}
                </p>
              </div>
              {isLive ? (
                <Badge tone="success">Live</Badge>
              ) : (
                canEdit && (
                  <Button
                    variant="secondary"
                    size="sm"
                    loading={publish.isPending && publish.variables === v.id}
                    onClick={() =>
                      window.confirm(
                        `Make v${v.version} live? New phone calls will use it immediately; calls in progress are not affected.`,
                      ) && publish.mutate(v.id)
                    }
                  >
                    {agent.published_version !== null && v.version < agent.published_version
                      ? "Roll back to this"
                      : "Publish"}
                  </Button>
                )
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
