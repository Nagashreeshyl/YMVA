"use client";

import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  Download,
  Pause,
  Play,
  Upload,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { useConfirm } from "@/components/dialog";
import {
  Badge,
  Button,
  ErrorNotice,
  Field,
  Input,
  Panel,
  Segmented,
  Skeleton,
} from "@/components/ui";
import {
  api,
  type Campaign,
  type CampaignContact,
  type CampaignInput,
  type ContactImportResult,
  type ContactStatus,
  type Page,
} from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useMe } from "@/lib/session";
import { CampaignForm } from "../campaign-form";
import { CampaignStatusBadge, progress } from "../campaigns-view";

const PAGE_SIZE = 50;
const CONTACT_STATUS: Record<
  ContactStatus,
  { label: string; tone: "neutral" | "success" | "warning" | "danger" | "gold" }
> = {
  pending: { label: "Waiting", tone: "neutral" },
  calling: { label: "Calling", tone: "gold" },
  retry: { label: "Will retry", tone: "warning" },
  done: { label: "Reached", tone: "success" },
  failed: { label: "Not reached", tone: "danger" },
};
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function ImportContacts({ campaignId }: { campaignId: string }) {
  const queryClient = useQueryClient();
  const [how, setHow] = useState<"file" | "sheet">("file");
  const [sheetUrl, setSheetUrl] = useState("");
  const [phoneColumn, setPhoneColumn] = useState("");
  const add = useMutation({
    mutationFn: (source: { csv?: string; sheet_url?: string }) =>
      api<ContactImportResult>(`/campaigns/${campaignId}/contacts`, {
        method: "POST",
        json: { ...source, phone_column: phoneColumn || null },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["campaign", campaignId] });
      queryClient.invalidateQueries({
        queryKey: ["campaign-contacts", campaignId],
      });
    },
  });
  return (
    <Panel
      title="Add contacts"
      description="First row: column titles. One column holds the phone number (named Phone, Mobile or Number); every other column becomes a detail the agent can use, e.g. Customer Name → {{customer_name}}."
    >
      <div className="flex flex-col gap-4">
        <Segmented
          label="Import from"
          value={how}
          onChange={setHow}
          options={[
            { value: "file", label: "CSV file" },
            { value: "sheet", label: "Google Sheet" },
          ]}
        />
        {how === "file" ? (
          <label className="flex w-fit cursor-pointer items-center gap-2 rounded-full border border-line-strong px-4 py-2 text-sm hover:bg-surface">
            <Upload className="size-4" aria-hidden /> Choose a CSV file
            <input
              type="file"
              accept=".csv,text/csv"
              className="sr-only"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (file) add.mutate({ csv: await file.text() });
              }}
            />
          </label>
        ) : (
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              add.mutate({ sheet_url: sheetUrl });
            }}
          >
            <Field
              label="Sheet link"
              hint="Share it as “Anyone with the link can view”. The tab in the link is used."
            >
              {(p) => (
                <Input
                  {...p}
                  className="w-[28rem] max-w-full"
                  placeholder="https://docs.google.com/spreadsheets/d/…"
                  value={sheetUrl}
                  onChange={(e) => setSheetUrl(e.target.value)}
                />
              )}
            </Field>
            <Button
              type="submit"
              variant="secondary"
              loading={add.isPending}
              disabled={!sheetUrl.includes("docs.google.com")}
            >
              Import
            </Button>
          </form>
        )}
        <Field
          label="Phone column (optional)"
          hint="Only if it isn't named Phone, Mobile or Number."
        >
          {(p) => (
            <Input
              {...p}
              className="w-64"
              value={phoneColumn}
              onChange={(e) => setPhoneColumn(e.target.value)}
            />
          )}
        </Field>
        {add.isPending && how === "file" && (
          <p className="text-[13px] text-muted">Importing…</p>
        )}
        {add.isError && <ErrorNotice error={add.error} />}
        {add.data && (
          <div
            className="rounded-lg bg-surface px-4 py-3 text-[13px]"
            role="status"
          >
            <p className="font-medium">
              Added {formatNumber(add.data.added)} contacts
              {add.data.duplicates > 0 &&
                ` · ${formatNumber(add.data.duplicates)} already in the list`}
              {add.data.invalid_count > 0 &&
                ` · ${formatNumber(add.data.invalid_count)} skipped`}
            </p>
            {add.data.columns.length > 0 && (
              <p className="mt-1 text-muted">
                Details for the agent:{" "}
                {add.data.columns.map((c) => `{{${c}}}`).join(", ")}
              </p>
            )}
            {add.data.invalid.length > 0 && (
              <ul className="mt-2 text-muted">
                {add.data.invalid.map((r) => (
                  <li key={r.row}>
                    Row {r.row}: “{r.value || "empty"}” — {r.reason}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </Panel>
  );
}

function Contacts({ campaignId, live }: { campaignId: string; live: boolean }) {
  const [filter, setFilter] = useState<ContactStatus | "all">("all");
  const [offset, setOffset] = useState(0);
  const contacts = useQuery({
    queryKey: ["campaign-contacts", campaignId, filter, offset],
    queryFn: () =>
      api<Page<CampaignContact>>(
        `/campaigns/${campaignId}/contacts?limit=${PAGE_SIZE}&offset=${offset}${filter === "all" ? "" : `&status=${filter}`}`,
      ),
    placeholderData: keepPreviousData,
    refetchInterval: live ? 5000 : false,
  });
  return (
    <Panel title="Contacts">
      <div className="mb-4">
        <Segmented
          label="Filter contacts"
          value={filter}
          onChange={(f) => {
            setFilter(f);
            setOffset(0);
          }}
          options={[
            { value: "all", label: "All" },
            { value: "pending", label: "Waiting" },
            { value: "retry", label: "Will retry" },
            { value: "done", label: "Reached" },
            { value: "failed", label: "Not reached" },
          ]}
        />
      </div>
      {contacts.isPending ? (
        <Skeleton className="h-40 rounded-lg" />
      ) : contacts.isError ? (
        <ErrorNotice
          error={contacts.error}
          onRetry={() => contacts.refetch()}
        />
      ) : contacts.data.total === 0 ? (
        <p className="text-[13px] text-muted">No contacts here yet.</p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-[13px]">
              <thead className="text-2xs text-muted">
                <tr className="border-b border-line">
                  <th className="py-2 pr-4 font-medium">Phone</th>
                  <th className="py-2 pr-4 font-medium">Details</th>
                  <th className="py-2 pr-4 font-medium">Status</th>
                  <th className="py-2 pr-4 font-medium">Result</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {contacts.data.items.map((c) => (
                  <tr key={c.id} className="align-top">
                    <td className="tabular py-2.5 pr-4 whitespace-nowrap">
                      {c.phone}
                    </td>
                    <td className="max-w-56 py-2.5 pr-4 text-muted">
                      <span className="line-clamp-2">
                        {Object.values(c.variables).slice(0, 3).join(" · ")}
                      </span>
                    </td>
                    <td className="py-2.5 pr-4 whitespace-nowrap">
                      <Badge tone={CONTACT_STATUS[c.status].tone}>
                        {CONTACT_STATUS[c.status].label}
                      </Badge>
                      {c.attempts > 0 && (
                        <span className="ml-2 text-2xs text-muted">
                          {c.attempts} tries
                        </span>
                      )}
                    </td>
                    <td className="py-2.5 pr-4">
                      {c.status === "done" ? (
                        <>
                          <span className="font-medium">
                            {c.outcome ?? "Writing summary…"}
                          </span>
                          {Object.entries(c.extracted).map(([k, v]) => (
                            <span key={k} className="block text-2xs text-muted">
                              {k}: {v}
                            </span>
                          ))}
                        </>
                      ) : (
                        <span className="text-muted">
                          {c.last_result ?? ""}
                          {c.status === "retry" &&
                            c.next_attempt_at &&
                            ` · next ${formatDateTime(c.next_attempt_at)}`}
                        </span>
                      )}
                      {c.call_id && (
                        <Link
                          href={`/calls/${c.call_id}`}
                          className="ml-2 text-2xs underline-offset-4 hover:underline"
                        >
                          Call
                        </Link>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-4 flex items-center justify-between text-[13px] text-muted">
            <span className="tabular">
              {formatNumber(offset + 1)}–
              {formatNumber(offset + contacts.data.items.length)} of{" "}
              {formatNumber(contacts.data.total)}
            </span>
            <div className="flex gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={offset === 0}
                onClick={() => setOffset(offset - PAGE_SIZE)}
              >
                Previous
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={offset + PAGE_SIZE >= contacts.data.total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next
              </Button>
            </div>
          </div>
        </>
      )}
    </Panel>
  );
}

export function CampaignView() {
  const { id } = useParams<{ id: string }>();
  const { data: me } = useMe();
  const canEdit = me?.role === "admin";
  const router = useRouter();
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const campaign = useQuery({
    queryKey: ["campaign", id],
    queryFn: () => api<Campaign>(`/campaigns/${id}`),
    refetchInterval: (q) => (q.state.data?.status === "running" ? 5000 : false),
  });
  const apply = (updated: Campaign) => {
    queryClient.setQueryData(["campaign", id], updated);
    queryClient.invalidateQueries({ queryKey: ["campaigns"] });
  };
  const start = useMutation({
    mutationFn: () =>
      api<Campaign>(`/campaigns/${id}/start`, { method: "POST" }),
    onSuccess: apply,
  });
  const pause = useMutation({
    mutationFn: () =>
      api<Campaign>(`/campaigns/${id}/pause`, { method: "POST" }),
    onSuccess: apply,
  });
  const save = useMutation({
    mutationFn: (body: CampaignInput) =>
      api<Campaign>(`/campaigns/${id}`, { method: "PUT", json: body }),
    onSuccess: (updated) => {
      apply(updated);
      setEditing(false);
    },
  });
  const remove = useMutation({
    mutationFn: () => api(`/campaigns/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["campaigns"] });
      router.push("/campaigns");
    },
  });

  if (campaign.isPending) return <Skeleton className="h-96 rounded-xl" />;
  if (campaign.isError)
    return (
      <ErrorNotice error={campaign.error} onRetry={() => campaign.refetch()} />
    );
  const c = campaign.data;
  const running = c.status === "running";
  const stats: [string, number][] = [
    ["Contacts", c.counts.total],
    ["Waiting", c.counts.pending],
    ["Calling now", c.counts.calling],
    ["Will retry", c.counts.retry],
    ["Reached", c.counts.done],
    ["Not reached", c.counts.failed],
  ];

  return (
    <>
      <Link
        href="/campaigns"
        className="mb-4 inline-flex items-center gap-1.5 text-[13px] text-muted hover:text-ink"
      >
        <ArrowLeft className="size-4" aria-hidden /> Campaigns
      </Link>
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="font-display text-[28px] leading-9 font-extrabold tracking-[-0.02em]">
              {c.name}
            </h1>
            <CampaignStatusBadge status={c.status} />
          </div>
          <p className="mt-1 text-muted">
            {c.agent_name ?? "Agent deleted"} ·{" "}
            {c.days.map((d) => DAY_NAMES[d]).join(", ")} · {c.start_time}–
            {c.end_time} · up to {c.max_concurrent} at a time · {c.max_attempts}{" "}
            tries, {c.retry_after_minutes} min apart
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <a
            href={`/api/campaigns/${id}/export.csv`}
            className="inline-flex h-10 items-center gap-2 rounded-full px-5 text-sm font-medium hover:bg-surface"
          >
            <Download className="size-4" aria-hidden /> Export results
          </a>
          {canEdit && !running && (
            <Button variant="ghost" onClick={() => setEditing(true)}>
              Edit
            </Button>
          )}
          {canEdit && !running && (
            <Button
              variant="danger"
              loading={remove.isPending}
              onClick={async () => {
                const { ok } = await confirm({
                  title: `Delete ${c.name}?`,
                  body: "Its contact list is deleted. Calls already made stay in Calls history.",
                  confirmLabel: "Delete",
                  tone: "danger",
                });
                if (ok) remove.mutate();
              }}
            >
              Delete
            </Button>
          )}
          {canEdit &&
            (running ? (
              <Button
                variant="secondary"
                loading={pause.isPending}
                onClick={() => pause.mutate()}
              >
                <Pause className="size-4" aria-hidden /> Pause
              </Button>
            ) : (
              c.status !== "completed" && (
                <Button
                  loading={start.isPending}
                  onClick={() => start.mutate()}
                >
                  <Play className="size-4" aria-hidden />{" "}
                  {c.status === "paused" ? "Resume" : "Start calling"}
                </Button>
              )
            ))}
        </div>
      </header>

      {[start, pause, save, remove].some((m) => m.isError) && (
        <div className="mb-4">
          <ErrorNotice
            error={start.error ?? pause.error ?? save.error ?? remove.error}
          />
        </div>
      )}
      {running && c.last_error && (
        <p
          className="mb-6 flex items-start gap-2 rounded-xl bg-warning-soft px-4 py-3 text-[13px] text-warning"
          role="status"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden /> Calls
          are on hold: {c.last_error}
        </p>
      )}

      {editing ? (
        <Panel title="Edit campaign" className="mb-6">
          <CampaignForm
            initial={c}
            submitLabel="Save changes"
            pending={save.isPending}
            error={save.error}
            onSubmit={(body) => save.mutate(body)}
            onCancel={() => setEditing(false)}
          />
        </Panel>
      ) : (
        <div className="mb-6 grid gap-6 lg:grid-cols-[1fr_320px]">
          <Panel title="Progress">
            <div className="mb-4 h-2 rounded-full bg-surface" aria-hidden>
              <div
                className="h-full rounded-full bg-chart-1"
                style={{ width: `${Math.round(progress(c) * 100)}%` }}
              />
            </div>
            <dl className="grid grid-cols-3 gap-4 sm:grid-cols-6">
              {stats.map(([label, n]) => (
                <div key={label}>
                  <dt className="text-2xs text-muted">{label}</dt>
                  <dd className="tabular text-xl font-semibold">
                    {formatNumber(n)}
                  </dd>
                </div>
              ))}
            </dl>
          </Panel>
          <Panel title="Outcomes">
            {Object.keys(c.outcomes).length === 0 ? (
              <p className="text-[13px] text-muted">
                Outcomes of reached customers appear here.
              </p>
            ) : (
              <ul className="flex flex-col gap-2 text-[13px]">
                {Object.entries(c.outcomes)
                  .sort((a, b) => b[1] - a[1])
                  .map(([outcome, n]) => (
                    <li key={outcome} className="flex justify-between gap-3">
                      <span>{outcome}</span>
                      <span className="tabular text-muted">
                        {formatNumber(n)}
                      </span>
                    </li>
                  ))}
              </ul>
            )}
          </Panel>
        </div>
      )}

      <div className="flex flex-col gap-6">
        {canEdit && <ImportContacts campaignId={id} />}
        <Contacts campaignId={id} live={running} />
      </div>
    </>
  );
}
