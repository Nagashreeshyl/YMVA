"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ArrowLeft, Check, Loader2 } from "lucide-react";
import { Buddy } from "@/components/buddy";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { AgentStatusBadge } from "@/components/agent-status";
import { useConfirm } from "@/components/dialog";
import { Button, ErrorNotice, Segmented, Skeleton } from "@/components/ui";
import { api, type AgentDetail, type AgentOptions } from "@/lib/api";
import { can } from "@/lib/permissions";
import { useMe } from "@/lib/session";
import { CallPhone } from "./call-phone";
import { Assistant } from "./assistant";
import { AgentEditor, type SaveState } from "./editor";
import { TestCall } from "./test-call";
import { Versions } from "./versions";

type Tab = "configure" | "test" | "call" | "versions";

function SaveIndicator({ state, error }: { state: SaveState; error: string | null }) {
  if (state === "pending" || state === "saving")
    return (
      <span className="inline-flex items-center gap-1.5 text-[13px] text-muted">
        <Loader2 className="size-3.5 animate-spin" aria-hidden /> Saving…
      </span>
    );
  if (state === "error")
    return (
      <span className="inline-flex items-center gap-1.5 text-[13px] text-danger" title={error ?? undefined}>
        <AlertCircle className="size-3.5" aria-hidden /> Not saved{error ? `: ${error}` : ""}
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1.5 text-[13px] text-muted">
      <Check className="size-3.5" aria-hidden /> All changes saved
    </span>
  );
}

export function AgentView() {
  const { id } = useParams<{ id: string }>();
  const { data: me } = useMe();
  const canEdit = can(me, "agents.edit");
  // Callers open an agent only to call with it: no settings, no versions.
  const canSee = can(me, "agents.view");
  const canCall = can(me, "calls.place");
  const queryClient = useQueryClient();
  const router = useRouter();
  const confirm = useConfirm();
  const [tab, setTab] = useState<Tab>(canSee ? "configure" : "call");
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [saveError, setSaveError] = useState<string | null>(null);
  // Bumped when the draft is replaced from outside the editor, so the editor reloads it.
  const [revision, setRevision] = useState(0);
  const agent = useQuery({ queryKey: ["agent", id], queryFn: () => api<AgentDetail>(`/agents/${id}`) });
  const options = useQuery({
    queryKey: ["agent-options"],
    queryFn: () => api<AgentOptions>("/agents/options"),
  });

  const apply = (updated: AgentDetail) => {
    queryClient.setQueryData(["agent", id], updated);
    queryClient.invalidateQueries({ queryKey: ["agents"] });
  };
  const saveVersion = useMutation({
    mutationFn: (note: string) =>
      api<AgentDetail>(`/agents/${id}/versions`, { method: "POST", json: { note } }),
    onSuccess: apply,
  });
  const publish = useMutation({
    mutationFn: (note: string) =>
      api<AgentDetail>(`/agents/${id}/publish`, { method: "POST", json: { note } }),
    onSuccess: apply,
  });
  const discard = useMutation({
    mutationFn: () => api<AgentDetail>(`/agents/${id}/draft`, { method: "DELETE" }),
    onSuccess: (updated) => {
      apply(updated);
      setRevision((r) => r + 1);
    },
  });
  const remove = useMutation({
    mutationFn: () => api(`/agents/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      router.push("/agents");
    },
  });
  const setStatus = useMutation({
    mutationFn: (status: "draft" | "active" | "paused" | "archived") =>
      api<AgentDetail>(`/agents/${id}`, { method: "PATCH", json: { status } }),
    onSuccess: apply,
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
  const busy = saveState === "pending" || saveState === "saving";
  const archived = a.status === "archived";
  const summary =
    a.published_version === null
      ? "Not published yet"
      : a.has_draft_changes
        ? `v${a.published_version} is live · you have unpublished edits`
        : a.has_unpublished_changes
          ? `v${a.published_version} is live · v${a.latest_version} is saved but not published`
          : `v${a.published_version} is live`;

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
            <Buddy seed={a.id} size={44} />
            <h1 className="font-display text-[28px] leading-9 font-extrabold tracking-[-0.02em]">{a.name}</h1>
            <AgentStatusBadge agent={a} />
          </div>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 text-muted">
            <span>{summary}</span>
            {canEdit && tab === "configure" && <SaveIndicator state={saveState} error={saveError} />}
          </p>
        </div>
        {canEdit && !archived && (
          <div className="flex flex-wrap gap-2">
            <Button
              variant="ghost"
              loading={setStatus.isPending && setStatus.variables === "archived"}
              onClick={async () => {
                const { ok } = await confirm({
                  title: `Archive ${a.name}?`,
                  body: "It stops answering phone numbers and moves to Archived. You can restore it any time; its calls stay in history.",
                  confirmLabel: "Archive",
                });
                if (ok) setStatus.mutate("archived");
              }}
            >
              Archive
            </Button>
            {a.status === "active" && (
              <Button
                variant="ghost"
                loading={setStatus.isPending}
                onClick={() => setStatus.mutate("paused")}
              >
                Pause
              </Button>
            )}
            {a.status === "paused" && (
              <Button
                variant="ghost"
                loading={setStatus.isPending}
                onClick={() => setStatus.mutate("active")}
              >
                Resume
              </Button>
            )}
            {a.has_draft_changes && (
              <Button
                variant="ghost"
                disabled={busy}
                loading={discard.isPending}
                onClick={async () => {
                  const { ok } = await confirm({
                    title: "Discard unsaved edits?",
                    body: `The editor goes back to version ${a.latest_version}.`,
                    confirmLabel: "Discard edits",
                    tone: "danger",
                  });
                  if (ok) discard.mutate();
                }}
              >
                Discard edits
              </Button>
            )}
            {a.has_draft_changes && (
              <Button
                variant="secondary"
                disabled={busy}
                loading={saveVersion.isPending}
                onClick={async () => {
                  const { ok, note } = await confirm({
                    title: `Save as version ${a.latest_version + 1}`,
                    body: "A saved version is a snapshot you can publish or return to later.",
                    note: { label: "What changed?", placeholder: "e.g. Softer tone for late payments" },
                    confirmLabel: "Save version",
                  });
                  if (ok) saveVersion.mutate(note);
                }}
              >
                Save as version
              </Button>
            )}
            {a.has_unpublished_changes && (
              <Button
                disabled={busy}
                loading={publish.isPending}
                onClick={async () => {
                  const { ok, note } = await confirm({
                    title: "Publish this agent?",
                    body: a.has_draft_changes
                      ? `Your edits are saved as version ${a.latest_version + 1} and go live for new phone calls.`
                      : `Version ${a.latest_version} goes live for new phone calls.`,
                    note: a.has_draft_changes
                      ? { label: "What changed?", placeholder: "Optional" }
                      : undefined,
                    confirmLabel: "Publish",
                  });
                  if (ok) publish.mutate(note);
                }}
              >
                Publish
              </Button>
            )}
          </div>
        )}
      </header>
      {archived && canEdit && (
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-surface px-5 py-4">
          <p>
            <span className="font-medium">This agent is archived.</span>{" "}
            <span className="text-muted">It can&apos;t take calls until you restore it.</span>
          </p>
          <div className="flex gap-2">
            <Button
              variant="danger"
              loading={remove.isPending}
              onClick={async () => {
                const { ok } = await confirm({
                  title: `Delete ${a.name} permanently?`,
                  body: "Its versions are deleted for good. Calls it made stay in history under its name. This can't be undone.",
                  confirmLabel: "Delete permanently",
                  tone: "danger",
                });
                if (ok) remove.mutate();
              }}
            >
              Delete permanently
            </Button>
            <Button
              variant="secondary"
              loading={setStatus.isPending}
              onClick={() => setStatus.mutate(a.published_version === null ? "draft" : "paused")}
            >
              Restore
            </Button>
          </div>
        </div>
      )}
      {[publish, saveVersion, discard, setStatus, remove].some((m) => m.isError) && (
        <div className="mb-4">
          <ErrorNotice
            error={publish.error ?? saveVersion.error ?? discard.error ?? setStatus.error ?? remove.error}
          />
        </div>
      )}
      <div className="mb-6">
        <Segmented
          label="Agent sections"
          value={tab}
          onChange={setTab}
          options={[
            ...(canSee ? [{ value: "configure" as const, label: "Configure" }] : []),
            ...(canCall
              ? [
                  { value: "test" as const, label: "Test in browser" },
                  { value: "call" as const, label: "Call a phone" },
                ]
              : []),
            ...(canSee
              ? [{ value: "versions" as const, label: `Versions (${a.versions.length})` }]
              : []),
          ]}
        />
      </div>
      {tab === "configure" && canSee && (
        <div
          className={
            canEdit && !archived
              ? "grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px] xl:items-start"
              : undefined
          }
        >
          <AgentEditor
            key={`${a.latest_version_id}:${revision}`}
            agent={a}
            options={options.data}
            readOnly={!canEdit}
            onSaveState={(state, error) => {
              setSaveState(state);
              setSaveError(error ?? null);
            }}
          />
          {canEdit && !archived && (
            <div className="order-first xl:sticky xl:top-6 xl:order-last">
              <Assistant
                agent={a}
                busy={busy}
                onChanged={(updated) => {
                  apply(updated);
                  setRevision((r) => r + 1);
                }}
              />
            </div>
          )}
        </div>
      )}
      {tab === "test" && canCall && <TestCall agent={a} />}
      {tab === "call" && canCall && <CallPhone agent={a} />}
      {tab === "versions" && canSee && (
        <Versions
          agent={a}
          canEdit={canEdit}
          onLoaded={() => {
            setRevision((r) => r + 1);
            setTab("configure");
          }}
        />
      )}
    </>
  );
}
