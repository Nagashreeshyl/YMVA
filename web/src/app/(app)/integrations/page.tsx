"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, CircleDashed, ExternalLink, Plus, XCircle } from "lucide-react";
import { useState } from "react";
import {
  Badge,
  Button,
  ErrorNotice,
  Field,
  Input,
  PageHeader,
  Panel,
  Select,
  Skeleton,
} from "@/components/ui";
import { CopyField } from "@/components/copy";
import { useConfirm } from "@/components/dialog";
import { api, type AIProviderStatus, type Integrations, type TelephonyAccount } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { useMe } from "@/lib/session";

const PROVIDER_LABEL: Record<string, string> = { plivo: "Plivo", vobiz: "Vobiz" };

function ConnectionBadge({
  verifiedAt,
  error,
  configured = true,
}: {
  verifiedAt: string | null;
  error: string | null;
  configured?: boolean;
}) {
  if (!configured)
    return (
      <Badge>
        <CircleDashed className="size-3.5" aria-hidden /> Not set up
      </Badge>
    );
  if (verifiedAt)
    return (
      <Badge tone="success">
        <CheckCircle2 className="size-3.5" aria-hidden /> Connected
      </Badge>
    );
  return (
    <Badge tone="danger">
      <XCircle className="size-3.5" aria-hidden /> {error ? "Check failed" : "Not verified"}
    </Badge>
  );
}

function AIKeyCard({ item, canEdit }: { item: AIProviderStatus; canEdit: boolean }) {
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(!item.configured);
  const [key, setKey] = useState("");
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["integrations"] });

  const save = useMutation({
    mutationFn: () =>
      api<AIProviderStatus>(`/integrations/ai/${item.provider}`, { method: "PUT", json: { api_key: key } }),
    onSuccess: (result) => {
      refresh();
      setKey("");
      if (result.verified_at) setEditing(false);
    },
  });
  const test = useMutation({
    mutationFn: () => api(`/integrations/ai/${item.provider}/test`, { method: "POST" }),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: () => api(`/integrations/ai/${item.provider}`, { method: "DELETE" }),
    onSuccess: () => {
      refresh();
      setEditing(true);
    },
  });

  return (
    <div className="flex flex-col gap-4 rounded-xl border border-line p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold">{item.label}</h3>
          <p className="text-[13px] text-muted">{item.purpose}</p>
        </div>
        <ConnectionBadge verifiedAt={item.verified_at} error={item.last_error} configured={item.configured} />
      </div>

      {item.configured && !editing && (
        <p className="text-[13px] text-muted">
          Key ending <span className="font-mono text-ink">{item.key_hint}</span>
          {item.verified_at && <> · checked {relativeTime(item.verified_at).toLowerCase()}</>}
        </p>
      )}
      {item.last_error && (
        <p className="rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">{item.last_error}</p>
      )}

      {canEdit && editing && (
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <Field label="API key" hint="Stored encrypted. It's never shown again after saving.">
            {(p) => (
              <Input
                {...p}
                type="password"
                autoComplete="off"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder="Paste the key"
                required
                minLength={8}
              />
            )}
          </Field>
          {save.isError && <ErrorNotice error={save.error} />}
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" size="sm" loading={save.isPending} disabled={key.trim().length < 8}>
              Save and test
            </Button>
            {item.configured && (
              <Button type="button" variant="ghost" size="sm" onClick={() => setEditing(false)}>
                Cancel
              </Button>
            )}
            <a
              href={item.key_url}
              target="_blank"
              rel="noreferrer"
              className="ml-auto inline-flex items-center gap-1 text-[13px] text-muted underline-offset-4 hover:text-ink hover:underline"
            >
              Get a key <ExternalLink className="size-3.5" aria-hidden />
            </a>
          </div>
        </form>
      )}

      {canEdit && item.configured && !editing && (
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" size="sm" loading={test.isPending} onClick={() => test.mutate()}>
            Test connection
          </Button>
          <Button variant="secondary" size="sm" onClick={() => setEditing(true)}>
            Replace key
          </Button>
          <Button
            variant="danger"
            size="sm"
            loading={remove.isPending}
            onClick={async () => {
              const { ok } = await confirm({
                title: `Remove the ${item.label} key?`,
                body: "Agents that use it will stop working until a new key is added.",
                confirmLabel: "Remove key",
                tone: "danger",
              });
              if (ok) remove.mutate();
            }}
          >
            Remove
          </Button>
        </div>
      )}
    </div>
  );
}

function AccountRow({ account, canEdit }: { account: TelephonyAccount; canEdit: boolean }) {
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["integrations"] });
  const test = useMutation({
    mutationFn: () => api(`/integrations/telephony/${account.id}/test`, { method: "POST" }),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: () => api(`/integrations/telephony/${account.id}`, { method: "DELETE" }),
    onSuccess: refresh,
  });

  return (
    <li className="flex flex-col gap-3 px-5 py-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <p className="font-medium">
            {account.name}{" "}
            <span className="font-normal text-muted">
              · {PROVIDER_LABEL[account.provider] ?? account.provider}
            </span>
          </p>
          <p className="text-[13px] text-muted">
            Auth ID {account.credential_hint} · {account.numbers}{" "}
            {account.numbers === 1 ? "number" : "numbers"} · up to {account.max_concurrent_calls} calls at
            once
          </p>
        </div>
        <ConnectionBadge verifiedAt={account.verified_at} error={account.last_error} />
        <Button variant="secondary" size="sm" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          {open ? "Hide setup" : "Inbound setup"}
        </Button>
        {canEdit && (
          <>
            <Button variant="secondary" size="sm" loading={test.isPending} onClick={() => test.mutate()}>
              Test
            </Button>
            <Button
              variant="danger"
              size="sm"
              onClick={async () => {
                const { ok } = await confirm({
                  title: `Disconnect ${account.name}?`,
                  body: "Calls can no longer be placed or received through this account.",
                  confirmLabel: "Disconnect",
                  tone: "danger",
                });
                if (ok) remove.mutate();
              }}
              loading={remove.isPending}
            >
              Disconnect
            </Button>
          </>
        )}
      </div>
      {account.last_error && (
        <p className="rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">{account.last_error}</p>
      )}
      {remove.isError && <ErrorNotice error={remove.error} />}
      {open && (
        <div className="flex flex-col gap-3 rounded-xl bg-rail p-4">
          <p className="text-[13px] text-muted">
            To answer incoming calls, create an application in your {PROVIDER_LABEL[account.provider]}{" "}
            console, paste these URLs (method POST), and link your numbers to it. Outbound calls need no
            setup.
          </p>
          <CopyField label="Answer URL" value={account.answer_url} />
          <CopyField label="Hangup / status URL" value={account.status_url} />
        </div>
      )}
    </li>
  );
}

function ConnectAccountForm({ providers, onDone }: { providers: string[]; onDone: () => void }) {
  const queryClient = useQueryClient();
  const add = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<TelephonyAccount>("/integrations/telephony", { method: "POST", json: body }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["integrations"] });
      onDone();
    },
  });
  return (
    <form
      className="grid gap-4 rounded-xl border border-line p-5 sm:grid-cols-2"
      onSubmit={(e) => {
        e.preventDefault();
        const f = new FormData(e.currentTarget);
        add.mutate({
          provider: f.get("provider"),
          name: f.get("name"),
          auth_id: f.get("auth_id"),
          auth_token: f.get("auth_token"),
          max_concurrent_calls: Number(f.get("max_concurrent_calls") || 10),
        });
      }}
    >
      <h3 className="font-semibold sm:col-span-2">Connect a telephony account</h3>
      <Field label="Provider">
        {(p) => (
          <Select {...p} name="provider" defaultValue={providers[0]}>
            {providers.map((id) => (
              <option key={id} value={id}>
                {PROVIDER_LABEL[id] ?? id}
              </option>
            ))}
          </Select>
        )}
      </Field>
      <Field label="Name" hint="For your reference, e.g. “Plivo – main”">
        {(p) => <Input {...p} name="name" required />}
      </Field>
      <Field label="Auth ID">{(p) => <Input {...p} name="auth_id" required autoComplete="off" />}</Field>
      <Field label="Auth Token" hint="Stored encrypted; never shown again.">
        {(p) => <Input {...p} name="auth_token" type="password" required autoComplete="off" />}
      </Field>
      <Field label="Max simultaneous calls" hint="Match the limit on your provider plan.">
        {(p) => (
          <Input {...p} name="max_concurrent_calls" type="number" min={1} max={500} defaultValue={10} />
        )}
      </Field>
      {add.isError && (
        <div className="sm:col-span-2">
          <ErrorNotice error={add.error} />
        </div>
      )}
      <div className="flex gap-2 sm:col-span-2">
        <Button type="submit" loading={add.isPending}>
          Connect and test
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

export default function IntegrationsPage() {
  const { data: me } = useMe();
  const canEdit = me?.role === "admin";
  const [connecting, setConnecting] = useState(false);
  const data = useQuery({ queryKey: ["integrations"], queryFn: () => api<Integrations>("/integrations") });

  return (
    <>
      <PageHeader
        title="Integrations"
        description="AI models and phone providers your agents use. Keys are encrypted at rest."
      />
      {data.isPending ? (
        <div className="flex flex-col gap-6">
          <Skeleton className="h-48 rounded-xl" />
          <Skeleton className="h-32 rounded-xl" />
        </div>
      ) : data.isError ? (
        <ErrorNotice error={data.error} onRetry={() => data.refetch()} />
      ) : (
        <div className="flex flex-col gap-8">
          <Panel
            title="AI models"
            description="Sarvam handles speech in Indian languages. Gemini holds the conversation, or does everything in Gemini Live mode."
          >
            <div className="grid gap-4 lg:grid-cols-2">
              {data.data.ai.map((item) => (
                <AIKeyCard key={item.provider} item={item} canEdit={canEdit} />
              ))}
            </div>
          </Panel>

          <Panel
            title="Telephony"
            description="Accounts used to place and receive phone calls."
            actions={
              canEdit && !connecting ? (
                <Button onClick={() => setConnecting(true)}>
                  <Plus className="size-4" aria-hidden /> Connect account
                </Button>
              ) : undefined
            }
          >
            <div className="flex flex-col gap-4">
              {!data.data.public_url_is_https && (
                <div className="flex gap-3 rounded-xl bg-warning-soft px-4 py-3 text-[13px] text-warning">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
                  <p>
                    Phone providers can&apos;t reach{" "}
                    <span className="font-mono">{data.data.public_base_url}</span>. Real calls need a public
                    HTTPS address: run <span className="font-mono">./ymva start --domain your.domain</span> on
                    a server, or <span className="font-mono">./ymva start --tunnel</span> to test from this
                    computer. Browser test calls work without it.
                  </p>
                </div>
              )}
              {connecting && (
                <ConnectAccountForm
                  providers={data.data.telephony_providers}
                  onDone={() => setConnecting(false)}
                />
              )}
              {data.data.telephony.length === 0 && !connecting ? (
                <p className="text-[13px] text-muted">
                  No accounts connected yet. Plivo and Vobiz are supported now; more providers are coming.
                </p>
              ) : (
                data.data.telephony.length > 0 && (
                  <ul className="divide-y divide-line rounded-xl border border-line">
                    {data.data.telephony.map((a) => (
                      <AccountRow key={a.id} account={a} canEdit={canEdit} />
                    ))}
                  </ul>
                )
              )}
            </div>
          </Panel>
        </div>
      )}
    </>
  );
}
