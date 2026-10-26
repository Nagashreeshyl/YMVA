"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { useState } from "react";
import {
  Button,
  ErrorNotice,
  Field,
  Input,
  Panel,
  Skeleton,
} from "@/components/ui";
import { api } from "@/lib/api";
import { formatNumber } from "@/lib/format";

interface Health {
  snapshot: {
    calls_active: number;
    calls_today: number;
    errors_last_15m: number;
    reply_p50_seconds: number | null;
    cost_today_usd: number;
    campaigns_running: number;
    dialer_idle_seconds: number | null;
    draining: boolean;
    backup_age_hours: number | null;
  };
  problems: Record<string, string>;
  alerting: boolean;
}

function Alerts() {
  const queryClient = useQueryClient();
  const current = useQuery({
    queryKey: ["alerts"],
    queryFn: () => api<{ webhook_url: string }>("/system/alerts"),
  });
  const [url, setUrl] = useState<string | null>(null);
  const value = url ?? current.data?.webhook_url ?? "";
  const save = useMutation({
    mutationFn: () =>
      api("/system/alerts", {
        method: "PUT",
        json: { webhook_url: value.trim() },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["alerts"] });
      queryClient.invalidateQueries({ queryKey: ["system-health"] });
      setUrl(null);
    },
  });
  const test = useMutation({
    mutationFn: () => api("/system/alerts/test", { method: "POST" }),
  });
  return (
    <Panel
      title="Alerts"
      description="When something goes wrong (calls failing, the dialer stuck, backups missing, a key or phone account failing its check), YMVA posts a message here, and again when it's fixed."
    >
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <Field
          label="Webhook address"
          hint="A Slack, Google Chat or Discord incoming-webhook URL. Leave empty to turn alerts off."
        >
          {(p) => (
            <Input
              {...p}
              type="url"
              placeholder="https://hooks.slack.com/services/…"
              value={value}
              onChange={(e) => setUrl(e.target.value)}
            />
          )}
        </Field>
        <div className="flex gap-2">
          <Button type="submit" loading={save.isPending}>
            Save
          </Button>
          <Button
            type="button"
            variant="secondary"
            disabled={!current.data?.webhook_url}
            loading={test.isPending}
            onClick={() => test.mutate()}
          >
            Send a test alert
          </Button>
        </div>
        {(save.isError || test.isError) && (
          <ErrorNotice error={save.error ?? test.error} />
        )}
        {test.isSuccess && (
          <p className="text-[13px] text-success">Sent. Check your channel.</p>
        )}
      </form>
    </Panel>
  );
}

export function HealthSettings({ canEdit }: { canEdit: boolean }) {
  const health = useQuery({
    queryKey: ["system-health"],
    queryFn: () => api<Health>("/system/health"),
    refetchInterval: 30_000,
  });
  if (health.isPending) return <Skeleton className="h-60 rounded-xl" />;
  if (health.isError)
    return (
      <ErrorNotice error={health.error} onRetry={() => health.refetch()} />
    );
  const { snapshot: s, problems } = health.data;
  const issues = Object.values(problems);
  const stats: [string, string][] = [
    ["Live calls", formatNumber(s.calls_active)],
    ["Calls today", formatNumber(s.calls_today)],
    [
      "Typical reply",
      s.reply_p50_seconds === null
        ? "—"
        : `${s.reply_p50_seconds.toFixed(2)} s`,
    ],
    ["Errors, last 15 min", formatNumber(s.errors_last_15m)],
    ["AI cost today", `$${s.cost_today_usd.toFixed(2)}`],
    [
      "Newest backup",
      s.backup_age_hours === null
        ? "None found"
        : `${Math.round(s.backup_age_hours)} h ago`,
    ],
    [
      "Campaign dialer",
      s.draining
        ? "Paused for an update"
        : s.dialer_idle_seconds === null
          ? "Not run yet"
          : `Ran ${Math.round(s.dialer_idle_seconds)} s ago`,
    ],
  ];
  return (
    <div className="flex flex-col gap-6">
      <Panel title="System health" description="Checked every 30 seconds.">
        {issues.length === 0 ? (
          <p className="mb-5 flex items-center gap-2 text-[13px] text-success">
            <CheckCircle2 className="size-4" aria-hidden /> Everything is
            working.
          </p>
        ) : (
          <ul className="mb-5 flex flex-col gap-2">
            {issues.map((issue) => (
              <li
                key={issue}
                className="flex items-start gap-2 rounded-lg bg-warning-soft px-3 py-2 text-[13px] text-warning"
              >
                <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />{" "}
                {issue}
              </li>
            ))}
          </ul>
        )}
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {stats.map(([label, value]) => (
            <div key={label}>
              <dt className="text-2xs text-muted">{label}</dt>
              <dd className="tabular font-medium">{value}</dd>
            </div>
          ))}
        </dl>
        {!health.data.alerting && (
          <p className="mt-4 text-2xs text-muted">
            Alerts are off: nobody is told when something breaks.
          </p>
        )}
      </Panel>
      {canEdit && <Alerts />}
    </div>
  );
}
