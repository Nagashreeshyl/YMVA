"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { History } from "lucide-react";
import { useState } from "react";
import { Button, EmptyState, ErrorNotice, Skeleton } from "@/components/ui";
import { api, type AuditLog, type Page } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";

const PAGE_SIZE = 25;

export function AuditLogList() {
  const [offset, setOffset] = useState(0);
  const logs = useQuery({
    queryKey: ["audit-logs", offset],
    queryFn: () =>
      api<Page<AuditLog>>(`/audit-logs?limit=${PAGE_SIZE}&offset=${offset}`),
    placeholderData: keepPreviousData,
  });

  if (logs.isPending) return <Skeleton className="h-64 rounded-xl" />;
  if (logs.isError)
    return <ErrorNotice error={logs.error} onRetry={() => logs.refetch()} />;
  if (logs.data.total === 0) {
    return (
      <EmptyState icon={History} title="No activity yet">
        Every sign-in and every change to agents, numbers, tools and people is
        recorded here.
      </EmptyState>
    );
  }

  const { items, total } = logs.data;
  return (
    <div className="flex flex-col gap-4">
      <ol className="divide-y divide-line rounded-xl border border-line">
        {items.map((log) => (
          <li
            key={log.id}
            className="flex flex-col gap-1 px-5 py-3.5 sm:flex-row sm:items-baseline sm:gap-6"
          >
            <div className="min-w-0 flex-1">
              <p>{log.summary}</p>
              {Object.keys(log.changes).length > 0 && (
                <ul className="mt-0.5 text-2xs text-muted">
                  {Object.entries(log.changes).map(([field, change]) => (
                    <li key={field}>
                      {field.replace(/_/g, " ")}:{" "}
                      {Array.isArray(change)
                        ? `${String(change[0])} → ${String(change[1])}`
                        : String(change)}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <p className="flex shrink-0 flex-wrap gap-x-2 text-2xs text-muted sm:flex-col sm:items-end sm:gap-0 sm:text-right">
              <span className="break-all">{log.actor_label}</span>
              <time className="tabular" dateTime={log.created_at}>
                {formatDateTime(log.created_at)}
              </time>
            </p>
          </li>
        ))}
      </ol>
      <div className="flex items-center justify-between text-[13px] text-muted">
        <span className="tabular">
          {formatNumber(offset + 1)}–{formatNumber(offset + items.length)} of{" "}
          {formatNumber(total)}
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
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Older
          </Button>
        </div>
      </div>
    </div>
  );
}
