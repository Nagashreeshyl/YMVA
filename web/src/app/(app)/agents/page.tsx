"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, ChevronRight, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AgentStatusBadge } from "@/components/agent-status";
import { Button, EmptyState, ErrorNotice, Field, Input, PageHeader, Select, Skeleton } from "@/components/ui";
import { api, type AgentDetail, type AgentOptions, type AgentSummary } from "@/lib/api";
import { LANGUAGE_NAMES, relativeTime } from "@/lib/format";
import { useMe } from "@/lib/session";

function NewAgentForm({ onDone }: { onDone: () => void }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const options = useQuery({
    queryKey: ["agent-options"],
    queryFn: () => api<AgentOptions>("/agents/options"),
  });
  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<AgentDetail>("/agents", { method: "POST", json: body }),
    onSuccess: (agent) => {
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      router.push(`/agents/${agent.id}`);
    },
  });
  return (
    <form
      className="grid gap-4 rounded-xl border border-line p-5 sm:grid-cols-[1fr_1fr_auto] sm:items-end"
      onSubmit={(e) => {
        e.preventDefault();
        const f = new FormData(e.currentTarget);
        create.mutate({ name: f.get("name"), template: f.get("template") });
      }}
    >
      <Field label="Agent name">
        {(p) => (
          <Input {...p} name="name" required autoFocus placeholder="e.g. Interest reminder – Karnataka" />
        )}
      </Field>
      <Field label="Start from">
        {(p) => (
          <Select {...p} name="template" defaultValue="interest_reminder">
            {(
              options.data?.templates ?? [{ id: "interest_reminder", name: "Interest payment reminder" }]
            ).map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </Select>
        )}
      </Field>
      <div className="flex gap-2">
        <Button type="submit" loading={create.isPending}>
          Create
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
      {create.isError && (
        <div className="sm:col-span-3">
          <ErrorNotice error={create.error} />
        </div>
      )}
    </form>
  );
}

export default function AgentsPage() {
  const { data: me } = useMe();
  const canEdit = me?.role === "admin";
  const [creating, setCreating] = useState(false);
  const agents = useQuery({ queryKey: ["agents"], queryFn: () => api<AgentSummary[]>("/agents") });

  return (
    <>
      <PageHeader
        title="Agents"
        description="Create, test and publish voice agents. Every change is saved as a new version."
        actions={
          canEdit && !creating ? (
            <Button onClick={() => setCreating(true)}>
              <Plus className="size-4" aria-hidden /> New agent
            </Button>
          ) : undefined
        }
      />
      <div className="flex flex-col gap-6">
        {creating && <NewAgentForm onDone={() => setCreating(false)} />}
        {agents.isPending ? (
          <Skeleton className="h-40 rounded-xl" />
        ) : agents.isError ? (
          <ErrorNotice error={agents.error} onRetry={() => agents.refetch()} />
        ) : agents.data.length === 0 ? (
          !creating && (
            <EmptyState
              icon={Bot}
              title="No agents yet"
              action={
                canEdit ? (
                  <Button onClick={() => setCreating(true)}>Create your first agent</Button>
                ) : undefined
              }
            >
              Start from the interest-reminder template: it already speaks English, Kannada, Telugu, Hindi and
              Tamil.
            </EmptyState>
          )
        ) : (
          <ul className="divide-y divide-line rounded-xl border border-line">
            {agents.data.map((a) => (
              <li key={a.id}>
                <Link
                  href={`/agents/${a.id}`}
                  className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-4 transition-colors hover:bg-rail"
                >
                  <div className="min-w-0 flex-1">
                    <p className="font-medium">{a.name}</p>
                    <p className="text-[13px] text-muted">
                      {a.mode === "gemini_live" ? "Gemini Live" : "Sarvam + Gemini"} ·{" "}
                      {a.languages.map((l) => LANGUAGE_NAMES[l] ?? l).join(", ")}
                    </p>
                  </div>
                  <span className="text-2xs text-muted">
                    v{a.latest_version}
                    {a.has_unpublished_changes &&
                      a.published_version !== null &&
                      ` · v${a.published_version} live`}{" "}
                    · {relativeTime(a.updated_at).toLowerCase()}
                  </span>
                  <AgentStatusBadge agent={a} />
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
