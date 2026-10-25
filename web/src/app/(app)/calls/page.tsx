"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ChevronRight, PhoneCall, X } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { CallStatusBadge, DirectionLabel } from "@/components/call-status";
import { Button, EmptyState, ErrorNotice, PageHeader, Segmented, Skeleton } from "@/components/ui";
import { api, type CallSummary, type Page } from "@/lib/api";
import { formatDateTime, formatDuration, formatNumber, LANGUAGE_NAMES } from "@/lib/format";

const PAGE_SIZE = 25;
type Filter = "all" | "outbound" | "inbound" | "web";

export default function CallsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64 rounded-xl" />}>
      <CallsList />
    </Suspense>
  );
}

function CallsList() {
  const [filter, setFilter] = useState<Filter>("all");
  const [offset, setOffset] = useState(0);
  // Set by the Overview's outcome links, e.g. /calls?outcome=Promised%20to%20pay
  const outcome = useSearchParams().get("outcome");
  const calls = useQuery({
    queryKey: ["calls", filter, offset, outcome],
    queryFn: () =>
      api<Page<CallSummary>>(
        `/calls?limit=${PAGE_SIZE}&offset=${offset}${filter === "all" ? "" : `&direction=${filter}`}${outcome ? `&outcome=${encodeURIComponent(outcome)}` : ""}`,
      ),
    placeholderData: keepPreviousData,
    refetchInterval: (q) =>
      q.state.data?.items.some((c) => ["ringing", "in_progress", "queued"].includes(c.status))
        ? 4000
        : 30_000,
  });

  return (
    <>
      <PageHeader title="Calls" description="Every call with its transcript, languages and outcome." />
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <Segmented
          label="Filter calls"
          value={filter}
          onChange={(f) => {
            setFilter(f);
            setOffset(0);
          }}
          options={[
            { value: "all", label: "All" },
            { value: "outbound", label: "Outgoing" },
            { value: "inbound", label: "Incoming" },
            { value: "web", label: "Browser tests" },
          ]}
        />
        {outcome && (
          <Link
            href="/calls"
            className="inline-flex h-8 items-center gap-1.5 rounded-full bg-gold-soft px-3 text-[13px] font-medium hover:opacity-80"
          >
            Outcome: {outcome}
            <X className="size-3.5" aria-label="Show all outcomes" />
          </Link>
        )}
      </div>
      {calls.isPending ? (
        <Skeleton className="h-64 rounded-xl" />
      ) : calls.isError ? (
        <ErrorNotice error={calls.error} onRetry={() => calls.refetch()} />
      ) : calls.data.total === 0 ? (
        <EmptyState icon={PhoneCall} title={filter === "all" ? "No calls yet" : "No calls match this filter"}>
          Calls appear here as soon as they start. Try one from an agent&apos;s “Test in browser” tab.
        </EmptyState>
      ) : (
        <div className="flex flex-col gap-4">
          <ul className="divide-y divide-line rounded-xl border border-line">
            {calls.data.items.map((c) => (
              <li key={c.id}>
                <Link
                  href={`/calls/${c.id}`}
                  className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1 px-5 py-3.5 transition-colors hover:bg-rail sm:grid-cols-[150px_1fr_auto_90px_auto]"
                >
                  <span className="tabular text-[13px] text-muted">{formatDateTime(c.created_at)}</span>
                  <span className="min-w-0 sm:order-none">
                    <span className="block truncate font-medium">
                      {c.direction === "web"
                        ? "Browser test"
                        : c.direction === "inbound"
                          ? c.from_number
                          : c.to_number}
                    </span>
                    <span className="flex flex-wrap items-center gap-x-2 text-[13px] text-muted">
                      <DirectionLabel direction={c.direction} />
                      {c.agent_name && <span>· {c.agent_name}</span>}
                      {c.languages.length > 0 && (
                        <span>· {c.languages.map((l) => LANGUAGE_NAMES[l] ?? l).join(", ")}</span>
                      )}
                      {c.outcome && <span className="font-medium text-ink">· {c.outcome}</span>}
                    </span>
                    {c.summary && <span className="mt-0.5 block truncate text-[13px] text-muted">{c.summary}</span>}
                  </span>
                  <CallStatusBadge status={c.status} />
                  <span className="tabular text-right text-[13px] text-muted">
                    {formatDuration(c.duration_seconds)}
                  </span>
                  <ChevronRight className="hidden size-4 text-faint sm:block" aria-hidden />
                </Link>
              </li>
            ))}
          </ul>
          <div className="flex items-center justify-between text-[13px] text-muted">
            <span className="tabular">
              {formatNumber(offset + 1)}–{formatNumber(offset + calls.data.items.length)} of{" "}
              {formatNumber(calls.data.total)}
            </span>
            <div className="flex gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={offset === 0}
                onClick={() => setOffset(offset - PAGE_SIZE)}
              >
                Newer
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={offset + PAGE_SIZE >= calls.data.total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Older
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
