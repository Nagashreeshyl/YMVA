"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, ChevronRight, Plus, Sparkles } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AgentStatusBadge } from "@/components/agent-status";
import {
  Button,
  EmptyState,
  ErrorNotice,
  Field,
  Input,
  PageHeader,
  Segmented,
  Select,
  Skeleton,
  Textarea,
} from "@/components/ui";
import { api, type AgentDetail, type AgentOptions, type AgentSummary } from "@/lib/api";
import { LANGUAGE_NAMES, relativeTime } from "@/lib/format";
import { useMe } from "@/lib/session";

const EXAMPLE_BRIEFS = [
  "Call customers whose gold loan matures this month. Ask if they want to renew or close it. If renewing, ask them to visit the branch with ID proof; if closing, tell them the amount to pay. Customers are mostly in Telangana.",
  "Answer incoming calls from people asking about gold loan rates and eligibility. Collect their name, city and how much gold they have, and say a branch executive will call back.",
];

function NewAgentForm({ onDone }: { onDone: () => void }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [how, setHow] = useState<"ai" | "template">("ai");
  const [brief, setBrief] = useState("");
  const options = useQuery({
    queryKey: ["agent-options"],
    queryFn: () => api<AgentOptions>("/agents/options"),
  });
  const opened = (agent: AgentDetail) => {
    queryClient.invalidateQueries({ queryKey: ["agents"] });
    router.push(`/agents/${agent.id}`);
  };
  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<AgentDetail>("/agents", { method: "POST", json: body }),
    onSuccess: opened,
  });
  const generate = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<AgentDetail>("/agents/generate", { method: "POST", json: body }),
    onSuccess: opened,
  });
  const error = how === "ai" ? generate.error : create.error;

  return (
    <div className="flex flex-col gap-5 rounded-xl border border-line p-5">
      <Segmented
        label="How to start"
        value={how}
        onChange={setHow}
        options={[
          { value: "ai", label: "Describe it" },
          { value: "template", label: "Start from a template" },
        ]}
      />
      {how === "ai" ? (
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            generate.mutate({ description: brief, name: String(f.get("name") ?? "") || null });
          }}
        >
          <Field
            label="What should the agent do?"
            hint="Say who it calls or answers, the goal, and what to do in each situation. Mention languages, region or voice if they matter. You can refine it afterwards by chatting with the assistant."
          >
            {(p) => (
              <Textarea
                {...p}
                autoFocus
                required
                minLength={10}
                maxLength={6000}
                rows={5}
                value={brief}
                onChange={(e) => setBrief(e.target.value)}
                placeholder="e.g. Remind customers that their gold loan interest is due. Confirm when they will pay; if they already paid, ask for the date…"
              />
            )}
          </Field>
          {!brief && (
            <div className="flex flex-col gap-2">
              <p className="text-2xs text-muted">Or start from an example:</p>
              {EXAMPLE_BRIEFS.map((example) => (
                <button
                  key={example}
                  type="button"
                  onClick={() => setBrief(example)}
                  className="rounded-lg bg-surface px-3 py-2 text-left text-[13px] hover:bg-surface-hover"
                >
                  {example}
                </button>
              ))}
            </div>
          )}
          <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
            <Field label="Name (optional)" hint="Leave empty and the AI names it.">
              {(p) => <Input {...p} name="name" maxLength={120} placeholder="e.g. Maturity reminder – Telangana" />}
            </Field>
            <div className="flex gap-2">
              <Button type="submit" loading={generate.isPending} disabled={brief.trim().length < 10}>
                {!generate.isPending && <Sparkles className="size-4" aria-hidden />}
                {generate.isPending ? "Writing your agent…" : "Build agent"}
              </Button>
              <Button type="button" variant="ghost" onClick={onDone} disabled={generate.isPending}>
                Cancel
              </Button>
            </div>
          </div>
          {generate.isPending && (
            <p className="text-[13px] text-muted" aria-live="polite">
              Writing the instructions, greetings and call details. This takes about 10 seconds.
            </p>
          )}
        </form>
      ) : (
        <form
          className="grid gap-4 sm:grid-cols-[1fr_1fr_auto] sm:items-end"
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
        </form>
      )}
      {error && <ErrorNotice error={error} />}
    </div>
  );
}

export default function AgentsPage() {
  const { data: me } = useMe();
  const canEdit = me?.role === "admin";
  const [creating, setCreating] = useState(false);
  const [view, setView] = useState<"active" | "archived">("active");
  const agents = useQuery({
    queryKey: ["agents", view],
    queryFn: () => api<AgentSummary[]>(view === "archived" ? "/agents?archived=true" : "/agents"),
  });

  return (
    <>
      <PageHeader
        title="Agents"
        description="Create, test and publish voice agents. Edits save automatically; you choose when to save a version."
        actions={
          canEdit && !creating ? (
            <Button onClick={() => setCreating(true)}>
              <Plus className="size-4" aria-hidden /> New agent
            </Button>
          ) : undefined
        }
      />
      <div className="flex flex-col gap-6">
        <Segmented
          label="Which agents"
          value={view}
          onChange={setView}
          options={[
            { value: "active", label: "Agents" },
            { value: "archived", label: "Archived" },
          ]}
        />
        {creating && <NewAgentForm onDone={() => setCreating(false)} />}
        {agents.isPending ? (
          <Skeleton className="h-40 rounded-xl" />
        ) : agents.isError ? (
          <ErrorNotice error={agents.error} onRetry={() => agents.refetch()} />
        ) : agents.data.length === 0 && view === "archived" ? (
          <p className="text-[13px] text-muted">No archived agents.</p>
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
              Describe what the agent should do in plain words and the AI writes it, or start from the
              interest-reminder template.
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
