"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useConfirm } from "@/components/dialog";
import { Badge, Button, ErrorNotice } from "@/components/ui";
import { api, type AgentDetail } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

export function Versions({
  agent,
  canEdit,
  onLoaded,
}: {
  agent: AgentDetail;
  canEdit: boolean;
  onLoaded: () => void;
}) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const apply = (updated: AgentDetail) => {
    queryClient.setQueryData(["agent", agent.id], updated);
    queryClient.invalidateQueries({ queryKey: ["agents"] });
  };
  const publish = useMutation({
    mutationFn: (versionId: string) =>
      api<AgentDetail>(`/agents/${agent.id}/publish`, { method: "POST", json: { version_id: versionId } }),
    onSuccess: apply,
  });
  const restore = useMutation({
    mutationFn: (versionId: string) =>
      api<AgentDetail>(`/agents/${agent.id}/versions/${versionId}/restore`, { method: "POST" }),
    onSuccess: (updated) => {
      apply(updated);
      onLoaded();
    },
  });

  return (
    <div className="flex flex-col gap-4">
      <p className="max-w-[70ch] text-[13px] text-muted">
        A version is a saved snapshot of the agent. Edits are kept as a draft until you choose “Save as
        version” or “Publish”. Phone calls always use the live version.
      </p>
      {(publish.isError || restore.isError) && <ErrorNotice error={publish.error ?? restore.error} />}
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
              {isLive && <Badge tone="success">Live</Badge>}
              {canEdit && (
                <>
                  <Button
                    variant="secondary"
                    size="sm"
                    loading={restore.isPending && restore.variables === v.id}
                    onClick={async () => {
                      if (agent.has_draft_changes) {
                        const { ok } = await confirm({
                          title: `Load v${v.version} into the editor?`,
                          body: "Your unsaved draft changes will be replaced. Save them as a version first if you want to keep them.",
                          confirmLabel: "Replace draft",
                          tone: "danger",
                        });
                        if (!ok) return;
                      }
                      restore.mutate(v.id);
                    }}
                  >
                    Load into editor
                  </Button>
                  {!isLive && (
                    <Button
                      variant="secondary"
                      size="sm"
                      loading={publish.isPending && publish.variables === v.id}
                      onClick={async () => {
                        const { ok } = await confirm({
                          title: `Make v${v.version} live?`,
                          body: "New phone calls use it immediately. Calls already in progress aren't affected.",
                          confirmLabel: `Publish v${v.version}`,
                        });
                        if (ok) publish.mutate(v.id);
                      }}
                    >
                      Publish
                    </Button>
                  )}
                </>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
