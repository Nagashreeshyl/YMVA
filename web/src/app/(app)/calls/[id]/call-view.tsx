"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Languages, Wrench } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { CallStatusBadge, DirectionLabel } from "@/components/call-status";
import { RecordingPlayer } from "@/components/recording-player";
import { Badge, Button, ErrorNotice, Panel, Skeleton } from "@/components/ui";
import { api, type CallDetail, type CallEvent } from "@/lib/api";
import { formatDateTime, formatDuration, formatNumber, humanize, LANGUAGE_NAMES } from "@/lib/format";
import { useMe } from "@/lib/session";

const LIVE = ["queued", "ringing", "in_progress"];

const END_REASON: Record<string, string> = {
  agent_ended: "Agent ended the call",
  caller_hung_up: "Caller hung up",
  transferred: "Transferred to a person",
  caller_silent: "Caller stayed silent",
  max_duration: "Reached the time limit",
  ended_from_dashboard: "Ended from the dashboard",
  setup_failed: "Couldn't start",
  provider_rejected: "Provider rejected the call",
  error: "Stopped by an error",
};

function Turn({ event }: { event: CallEvent }) {
  const lang = event.language ? (LANGUAGE_NAMES[event.language] ?? event.language) : null;
  if (event.type === "user_turn" || event.type === "agent_turn") {
    const agent = event.type === "agent_turn";
    return (
      <div className={agent ? "max-w-[85%] self-start" : "max-w-[85%] self-end"}>
        <p className={`mb-0.5 text-2xs text-muted ${agent ? "" : "text-right"}`}>
          {agent ? "Agent" : "Customer"}
          {lang && ` · ${lang}`}
          {Boolean(event.data.interrupted) && " · interrupted"}
        </p>
        <p
          className={
            agent
              ? "rounded-2xl rounded-tl-md bg-rail px-3.5 py-2"
              : "rounded-2xl rounded-tr-md bg-ink px-3.5 py-2 text-on-ink"
          }
        >
          {event.content}
        </p>
      </div>
    );
  }
  const icon =
    event.type === "language_switch" ? (
      <Languages className="size-3.5" aria-hidden />
    ) : event.type === "tool_call" ? (
      <Wrench className="size-3.5" aria-hidden />
    ) : null;
  const text =
    event.type === "language_switch"
      ? `Switched language: ${event.content}`
      : event.type === "tool_call"
        ? `Used tool: ${event.content}`
        : event.content;
  return (
    <p
      className={`inline-flex items-center gap-1.5 self-center text-2xs ${event.type === "error" ? "text-danger" : "text-muted"}`}
    >
      {icon}
      {text}
    </p>
  );
}

function Metric({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="flex flex-col">
      <dt className="text-2xs text-muted">{label}</dt>
      <dd className="tabular font-medium">{value}</dd>
      {note && <dd className="text-2xs text-muted">{note}</dd>}
    </div>
  );
}

export function CallView() {
  const { id } = useParams<{ id: string }>();
  const { data: me } = useMe();
  const queryClient = useQueryClient();
  const call = useQuery({
    queryKey: ["call", id],
    queryFn: () => api<CallDetail>(`/calls/${id}`),
    // Keep checking while the call is live, and until its summary has been written.
    refetchInterval: (q) =>
      q.state.data &&
      (LIVE.includes(q.state.data.status) ||
        (!q.state.data.summary && q.state.data.events.length > 0 && Date.now() - Date.parse(q.state.data.ended_at ?? "") < 60_000))
        ? 2500
        : false,
  });
  const hangup = useMutation({
    mutationFn: () => api(`/calls/${id}/hangup`, { method: "POST" }),
    onSuccess: () => call.refetch(),
  });
  const analyze = useMutation({
    mutationFn: () => api<CallDetail>(`/calls/${id}/analyze`, { method: "POST" }),
    onSuccess: (updated) => queryClient.setQueryData(["call", id], updated),
  });

  if (call.isPending) return <Skeleton className="h-96 rounded-xl" />;
  if (call.isError) return <ErrorNotice error={call.error} onRetry={() => call.refetch()} />;
  const c = call.data;
  const response = c.latency.response;
  const usage = c.cost_breakdown.usage;
  const title =
    c.direction === "web"
      ? "Browser test call"
      : c.direction === "inbound"
        ? `Call from ${c.from_number}`
        : `Call to ${c.to_number}`;

  return (
    <>
      <Link
        href="/calls"
        className="mb-4 inline-flex items-center gap-1.5 text-[13px] text-muted hover:text-ink"
      >
        <ArrowLeft className="size-4" aria-hidden /> Calls
      </Link>
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="font-display text-[28px] leading-9 font-extrabold tracking-[-0.02em]">{title}</h1>
            <CallStatusBadge status={c.status} />
          </div>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 text-muted">
            <DirectionLabel direction={c.direction} />
            <span>· {formatDateTime(c.created_at)}</span>
            {c.agent_name && (
              <span>
                ·{" "}
                <Link
                  href={`/agents/${c.agent_id}`}
                  className="underline-offset-4 hover:text-ink hover:underline"
                >
                  {c.agent_name}
                </Link>
              </span>
            )}
          </p>
        </div>
        {me?.role === "admin" && LIVE.includes(c.status) && (
          <Button variant="secondary" loading={hangup.isPending} onClick={() => hangup.mutate()}>
            End call
          </Button>
        )}
      </header>

      {c.has_recording && (
        <Panel title="Recording" className="mb-6">
          <RecordingPlayer
            src={`/api/calls/${c.id}/recording`}
            downloadName={`call-${c.created_at.slice(0, 10)}-${c.id.slice(0, 8)}.wav`}
          />
        </Panel>
      )}
      {!LIVE.includes(c.status) && (
        <Panel
          title="Summary"
          className="mb-6"
          actions={
            me?.role === "admin" && c.events.length > 0 ? (
              <Button variant="ghost" size="sm" loading={analyze.isPending} onClick={() => analyze.mutate()}>
                {c.summary ? "Analyse again" : "Analyse now"}
              </Button>
            ) : undefined
          }
        >
          {c.summary ? (
            <div className="flex flex-col gap-4">
              <div className="flex flex-wrap items-start gap-3">
                {c.outcome && <Badge tone="gold">{c.outcome}</Badge>}
                <p className="max-w-[70ch] flex-1">{c.summary}</p>
              </div>
              {Object.keys(c.extracted).length > 0 && (
                <dl className="grid gap-3 sm:grid-cols-3">
                  {Object.entries(c.extracted).map(([name, value]) => (
                    <Metric key={name} label={name} value={value} />
                  ))}
                </dl>
              )}
            </div>
          ) : (
            <p className="text-[13px] text-muted">
              The summary and outcome appear a few seconds after the call ends.
            </p>
          )}
          {analyze.isError && (
            <div className="mt-3">
              <ErrorNotice error={analyze.error} />
            </div>
          )}
        </Panel>
      )}
      <div className="grid items-start gap-6 lg:grid-cols-[1fr_320px]">
        <Panel title="Conversation">
          {c.events.length === 0 ? (
            <p className="text-[13px] text-muted">
              {LIVE.includes(c.status)
                ? "Waiting for the call to connect…"
                : "No conversation was recorded for this call."}
            </p>
          ) : (
            <div className="flex flex-col gap-3">
              {c.events.map((e) => (
                <Turn key={e.seq} event={e} />
              ))}
            </div>
          )}
        </Panel>

        <div className="flex flex-col gap-6">
          <Panel title="Details">
            <dl className="grid grid-cols-2 gap-4">
              <Metric label="Duration" value={formatDuration(c.duration_seconds)} />
              <Metric
                label="Ended because"
                value={
                  c.end_reason ? (END_REASON[c.end_reason] ?? humanize(c.end_reason.toLowerCase())) : "—"
                }
              />
              <Metric
                label="Languages"
                value={c.languages.map((l) => LANGUAGE_NAMES[l] ?? l).join(", ") || "—"}
              />
              <Metric
                label="Provider"
                value={c.provider ? humanize(c.provider) : c.direction === "web" ? "Browser" : "—"}
              />
              <Metric
                label="Response time"
                value={response ? `${(response.p50_ms / 1000).toFixed(2)} s` : "—"}
                note={response ? `typical · slowest ${(response.p95_ms / 1000).toFixed(2)} s` : undefined}
              />
              {c.cost_total !== null && (
                <Metric
                  label="AI cost (estimate)"
                  value={`$${c.cost_total.toFixed(4)}`}
                  note={`≈ ₹${(c.cost_total * 88).toFixed(2)} · phone charges not included`}
                />
              )}
              {usage && (
                <Metric
                  label="AI usage"
                  value={`${formatNumber(Math.round(usage.llm_prompt_tokens + usage.llm_completion_tokens))} tokens`}
                  note={`${formatNumber(Math.round(usage.tts_characters))} characters spoken`}
                />
              )}
            </dl>
            {c.error && (
              <p className="mt-4 rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">{c.error}</p>
            )}
          </Panel>
          {Object.keys(c.variables).length > 0 && (
            <Panel title="Customer details">
              <dl className="flex flex-col gap-2 text-[13px]">
                {Object.entries(c.variables).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-3">
                    <dt className="text-muted">{k}</dt>
                    <dd className="text-right break-all">{v || "—"}</dd>
                  </div>
                ))}
              </dl>
            </Panel>
          )}
        </div>
      </div>
    </>
  );
}
