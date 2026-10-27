"use client";

import { useQuery } from "@tanstack/react-query";
import { Check, Circle } from "lucide-react";
import Link from "next/link";
import { DailyCallsChart } from "@/components/daily-calls-chart";
import { ErrorNotice, PageHeader, Skeleton } from "@/components/ui";
import { api, type Overview } from "@/lib/api";
import {
  formatDuration,
  formatNumber,
  formatPercent,
  humanize,
  LANGUAGE_NAMES,
  USD_TO_INR,
} from "@/lib/format";
import { cn } from "@/lib/cn";
import { useMe } from "@/lib/session";

function Stat({
  label,
  value,
  note,
  className,
}: {
  label: string;
  value: string;
  note?: string;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-1 border-line px-5 py-4", className)}>
      <dt className="text-[13px] text-muted">{label}</dt>
      <dd className="tabular text-2xl leading-8 font-semibold tracking-[-0.01em]">{value}</dd>
      {note && <dd className="text-2xs text-muted">{note}</dd>}
    </div>
  );
}

function Panel({
  title,
  children,
  className,
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-line p-5 ${className ?? ""}`}>
      <h2 className="mb-4 text-[15px] font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function Breakdown({
  data,
  label,
  empty,
  href,
}: {
  data: Record<string, number>;
  label: (k: string) => string;
  empty: string;
  /** Makes each row a link, e.g. to the calls with that outcome. */
  href?: (k: string) => string;
}) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((sum, [, n]) => sum + n, 0);
  if (entries.length === 0) return <p className="text-[13px] text-muted">{empty}</p>;
  return (
    <ul className="flex flex-col gap-3">
      {entries.map(([key, n]) => (
        <li key={key} className="flex flex-col gap-1.5">
          <div className="flex items-baseline justify-between gap-3 text-[13px]">
            {href ? (
              <Link href={href(key)} className="underline-offset-4 hover:underline">
                {label(key)}
              </Link>
            ) : (
              <span>{label(key)}</span>
            )}
            <span className="tabular text-muted">
              {formatNumber(n)} · {formatPercent(n / total)}
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-surface">
            <div className="h-full rounded-full bg-chart-1" style={{ width: `${(n / total) * 100}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function SetupChecklist({ data }: { data: Overview }) {
  const steps = [
    {
      done: data.providers_connected > 0,
      label: "Connect a telephony provider",
      detail: "Plivo, Vobiz, Exotel, Twilio, Telnyx or Vonage",
      href: "/integrations",
    },
    {
      done: data.phone_numbers > 0,
      label: "Add a phone number",
      detail: "Used for inbound calls and as caller ID",
      href: "/numbers",
    },
    {
      done: data.agents_total > 0,
      label: "Create your first agent",
      detail: "Describe it in plain English or build it manually",
      href: "/agents",
    },
    {
      done: data.calls_7d > 0,
      label: "Make a test call",
      detail: "Talk to the agent from your browser or phone",
      href: "/calls",
    },
  ];
  const remaining = steps.filter((s) => !s.done).length;
  if (remaining === 0) return null;
  return (
    <Panel title="Get set up">
      <ol className="flex flex-col">
        {steps.map((step) => (
          <li key={step.label}>
            <Link
              href={step.href}
              className="-mx-2 flex items-start gap-3 rounded-lg px-2 py-2.5 transition-colors hover:bg-surface"
            >
              {step.done ? (
                <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-ink text-on-ink">
                  <Check className="size-3" strokeWidth={3} aria-hidden />
                </span>
              ) : (
                <Circle className="mt-0.5 size-5 shrink-0 text-line-strong" strokeWidth={1.75} aria-hidden />
              )}
              <span className="flex flex-col">
                <span className={step.done ? "text-muted line-through" : "font-medium"}>{step.label}</span>
                <span className="text-2xs text-muted">{step.detail}</span>
              </span>
            </Link>
          </li>
        ))}
      </ol>
    </Panel>
  );
}

function OverviewSkeleton() {
  return (
    <div className="flex flex-col gap-6" aria-busy>
      <Skeleton className="h-[108px] w-full rounded-xl" />
      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <Skeleton className="h-[290px] rounded-xl" />
        <Skeleton className="h-[290px] rounded-xl" />
      </div>
    </div>
  );
}

function greeting(): string {
  const hour = Number(
    new Intl.DateTimeFormat("en-IN", { hour: "numeric", hour12: false, timeZone: "Asia/Kolkata" }).format(
      new Date(),
    ),
  );
  return hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
}

export default function OverviewPage() {
  const { data: me } = useMe();
  const overview = useQuery({
    queryKey: ["overview"],
    queryFn: () => api<Overview>("/overview"),
    refetchInterval: 30_000,
  });

  return (
    <>
      <PageHeader
        title="Overview"
        description={me ? `${greeting()}, ${me.name.split(" ")[0]}. Here's how your agents are doing.` : " "}
      />
      {overview.isPending ? (
        <OverviewSkeleton />
      ) : overview.isError ? (
        <ErrorNotice error={overview.error} onRetry={() => overview.refetch()} />
      ) : (
        <div className="flex flex-col gap-6">
          <dl className="grid grid-cols-2 overflow-hidden rounded-xl border border-line md:grid-cols-5">
            <Stat
              className="col-span-2 md:col-span-1"
              label="Live now"
              value={formatNumber(overview.data.active_calls)}
              note={overview.data.active_calls === 1 ? "call in progress" : "calls in progress"}
            />
            <Stat
              className="border-t md:border-t-0 md:border-l"
              label="Calls today"
              value={formatNumber(overview.data.calls_today)}
            />
            <Stat
              className="border-t border-l md:border-t-0"
              label="Connect rate"
              value={formatPercent(overview.data.connect_rate_7d)}
              note="last 7 days"
            />
            <Stat
              className="border-t md:border-t-0 md:border-l"
              label="Avg. duration"
              value={formatDuration(overview.data.avg_duration_seconds_7d)}
              note="last 7 days"
            />
            <Stat
              className="border-t border-l md:border-t-0"
              label="Cost per minute"
              value={
                overview.data.cost_per_minute_7d === null
                  ? "—"
                  : `₹${(overview.data.cost_per_minute_7d * USD_TO_INR).toFixed(2)}`
              }
              note={`AI + phone line · ₹${(overview.data.cost_7d * USD_TO_INR).toFixed(0)} in 7 days`}
            />
          </dl>

          <div className="grid items-start gap-6 lg:grid-cols-[1fr_340px]">
            <Panel title="Calls, last 7 days">
              <DailyCallsChart data={overview.data.daily} />
            </Panel>
            <div className="flex flex-col gap-6">
              {me?.role === "admin" && <SetupChecklist data={overview.data} />}
              <Panel title="Agents">
                <p className="tabular text-2xl font-semibold">
                  {overview.data.agents_active}
                  <span className="text-base font-normal text-muted">
                    {" "}
                    active of {overview.data.agents_total}
                  </span>
                </p>
              </Panel>
            </div>
          </div>

          <div className="grid gap-6 md:grid-cols-2">
            <Panel title="Outcomes, last 7 days">
              <Breakdown
                data={overview.data.outcomes_7d}
                label={humanize}
                href={(outcome) => `/calls?outcome=${encodeURIComponent(outcome)}`}
                empty="Each call’s outcome, such as promised to pay or call back later, is counted here after it ends."
              />
            </Panel>
            <Panel title="Languages spoken, last 7 days">
              <Breakdown
                data={overview.data.languages_7d}
                label={(code) => LANGUAGE_NAMES[code] ?? code.toUpperCase()}
                empty="Each call's detected languages (Kannada, Telugu, Hindi…) are tallied here."
              />
            </Panel>
          </div>
        </div>
      )}
    </>
  );
}
