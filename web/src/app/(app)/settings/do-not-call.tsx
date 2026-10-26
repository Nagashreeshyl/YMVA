"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  Button,
  ErrorNotice,
  Field,
  Input,
  Panel,
  Skeleton,
  Textarea,
} from "@/components/ui";
import { api } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";

interface Entry {
  phone: string;
  reason: string;
  created_at: string;
}

export function DoNotCallSettings({ canEdit }: { canEdit: boolean }) {
  const queryClient = useQueryClient();
  const list = useQuery({
    queryKey: ["do-not-call"],
    queryFn: () => api<Entry[]>("/do-not-call"),
  });
  const [numbers, setNumbers] = useState("");
  const [reason, setReason] = useState("");
  const add = useMutation({
    mutationFn: () =>
      api<{ added: number }>("/do-not-call", {
        method: "POST",
        json: { numbers, reason },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["do-not-call"] });
      setNumbers("");
      setReason("");
    },
  });
  const remove = useMutation({
    mutationFn: (phone: string) =>
      api(`/do-not-call/${encodeURIComponent(phone)}`, { method: "DELETE" }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["do-not-call"] }),
  });

  return (
    <div className="flex flex-col gap-6">
      {canEdit && (
        <Panel
          title="Add numbers"
          description="Customers who asked not to be called. Campaigns skip these numbers and “Call a phone” refuses them. Incoming calls from them are still answered."
        >
          <form
            className="flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              add.mutate();
            }}
          >
            <Field
              label="Phone numbers"
              hint="One per line, or separated by commas. 10-digit Indian numbers are fine."
            >
              {(p) => (
                <Textarea
                  {...p}
                  rows={4}
                  placeholder={"98450 12345\n+91 98450 54321"}
                  value={numbers}
                  onChange={(e) => setNumbers(e.target.value)}
                />
              )}
            </Field>
            <Field label="Reason (optional)">
              {(p) => (
                <Input
                  {...p}
                  maxLength={200}
                  placeholder="Asked on the call not to be contacted"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              )}
            </Field>
            <div>
              <Button
                type="submit"
                loading={add.isPending}
                disabled={!numbers.trim()}
              >
                Add to list
              </Button>
            </div>
            {add.isError && <ErrorNotice error={add.error} />}
            {add.isSuccess && (
              <p className="text-[13px] text-success">
                Added {formatNumber(add.data.added)} number(s).
              </p>
            )}
          </form>
        </Panel>
      )}
      <Panel
        title="Do-not-call list"
        description={
          list.data ? `${formatNumber(list.data.length)} number(s)` : undefined
        }
      >
        {list.isPending ? (
          <Skeleton className="h-24 rounded-lg" />
        ) : list.isError ? (
          <ErrorNotice error={list.error} onRetry={() => list.refetch()} />
        ) : list.data.length === 0 ? (
          <p className="text-[13px] text-muted">No numbers yet.</p>
        ) : (
          <ul className="divide-y divide-line">
            {list.data.map((entry) => (
              <li
                key={entry.phone}
                className="flex items-center justify-between gap-3 py-2.5"
              >
                <div className="min-w-0">
                  <p className="tabular font-medium">{entry.phone}</p>
                  <p className="truncate text-2xs text-muted">
                    {entry.reason || "No reason given"} · added{" "}
                    {formatDateTime(entry.created_at)}
                  </p>
                </div>
                {canEdit && (
                  <Button
                    variant="secondary"
                    size="sm"
                    loading={
                      remove.isPending && remove.variables === entry.phone
                    }
                    onClick={() => remove.mutate(entry.phone)}
                    aria-label={`Remove ${entry.phone} from the do-not-call list`}
                  >
                    Remove
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
        {remove.isError && <ErrorNotice error={remove.error} />}
      </Panel>
    </div>
  );
}
