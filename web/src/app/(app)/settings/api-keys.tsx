"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plus } from "lucide-react";
import { useState } from "react";
import { CopyField } from "@/components/copy";
import { useConfirm } from "@/components/dialog";
import {
  Badge,
  Button,
  EmptyState,
  ErrorNotice,
  Field,
  Input,
  Select,
  Skeleton,
} from "@/components/ui";
import { api, type Role } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { can, ROLE_HINTS, ROLE_LABELS } from "@/lib/permissions";
import { useMe } from "@/lib/session";

interface ApiKey {
  id: string;
  name: string;
  key_prefix: string;
  role: Role;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

function NewKeyForm({ onCreated }: { onCreated: (key: string) => void }) {
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  const roles = (Object.keys(ROLE_LABELS) as Role[]).filter(
    (r) => r !== "owner" || can(me, "owners.manage"),
  );
  const [role, setRole] = useState<Role>("caller");
  const create = useMutation({
    mutationFn: (body: { name: string; role: Role }) =>
      api<ApiKey & { key: string }>("/api-keys", { method: "POST", json: body }),
    onSuccess: (made) => {
      queryClient.invalidateQueries({ queryKey: ["api-keys"] });
      onCreated(made.key);
    },
  });
  return (
    <form
      className="grid gap-4 rounded-xl border border-line p-5 sm:grid-cols-2"
      onSubmit={(e) => {
        e.preventDefault();
        const name = String(new FormData(e.currentTarget).get("name") ?? "");
        create.mutate({ name, role });
      }}
    >
      <h2 className="text-[15px] font-semibold sm:col-span-2">New API key</h2>
      <Field label="Name" hint="Which app uses it, e.g. n8n or Sales CRM.">
        {(p) => <Input {...p} name="name" required autoFocus maxLength={120} />}
      </Field>
      <Field label="Access" hint={ROLE_HINTS[role]}>
        {(p) => (
          <Select {...p} value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {roles.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABELS[r]}
              </option>
            ))}
          </Select>
        )}
      </Field>
      {create.isError && (
        <div className="sm:col-span-2">
          <ErrorNotice error={create.error} />
        </div>
      )}
      <div className="sm:col-span-2">
        <Button type="submit" loading={create.isPending}>
          Create key
        </Button>
      </div>
    </form>
  );
}

function KeyRow({ apiKey }: { apiKey: ApiKey }) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const { data: me } = useMe();
  const revoke = useMutation({
    mutationFn: () => api(`/api-keys/${apiKey.id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["api-keys"] }),
  });
  const revoked = apiKey.revoked_at !== null;
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
      <div className="min-w-0">
        <p className="font-medium">{apiKey.name}</p>
        <p className="text-[13px] text-muted">
          <code className="font-mono">{apiKey.key_prefix}…</code> · {ROLE_LABELS[apiKey.role]} ·
          last used {relativeTime(apiKey.last_used_at).toLowerCase()}
        </p>
      </div>
      {revoked ? (
        <Badge tone="danger">Revoked</Badge>
      ) : (
        (apiKey.role !== "owner" || can(me, "owners.manage")) && (
          <Button
            variant="secondary"
            size="sm"
            loading={revoke.isPending}
            onClick={async () => {
              const { ok } = await confirm({
                title: `Revoke ${apiKey.name}?`,
                body: "Apps using this key stop working immediately. This can't be undone.",
                confirmLabel: "Revoke",
                tone: "danger",
              });
              if (ok) revoke.mutate();
            }}
          >
            Revoke
          </Button>
        )
      )}
    </li>
  );
}

export function ApiKeySettings() {
  const [adding, setAdding] = useState(false);
  const [created, setCreated] = useState<string | null>(null);
  const keys = useQuery({ queryKey: ["api-keys"], queryFn: () => api<ApiKey[]>("/api-keys") });
  const origin = typeof window === "undefined" ? "" : window.location.origin;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-[60ch] text-muted">
          Let other apps (n8n, your CRM) start calls and read results. A key has the access of
          its role: a Caller key can only start calls and see the calls it started.
        </p>
        {!adding && !created && (
          <Button onClick={() => setAdding(true)}>
            <Plus className="size-4" aria-hidden />
            New API key
          </Button>
        )}
      </div>
      {adding && (
        <NewKeyForm
          onCreated={(key) => {
            setAdding(false);
            setCreated(key);
          }}
        />
      )}
      {created && (
        <div className="flex flex-col gap-3 rounded-xl border border-gold/40 p-5">
          <h2 className="text-[15px] font-semibold">Copy your key now</h2>
          <p className="text-[13px] text-muted">
            It won&apos;t be shown again. Store it in the app&apos;s credentials, not in chat or
            email.
          </p>
          <CopyField label="API key" value={created} />
          <CopyField
            label="Example: start a call"
            value={`curl -X POST ${origin}/api/calls/outbound -H "Authorization: Bearer ${created}" -H "Content-Type: application/json" -d '{"agent_id":"<agent id>","to":"+9198xxxxxxxx","variables":{"customer_name":"Ramesh"},"callback_url":"https://your-app/webhook"}'`}
          />
          <div>
            <Button variant="secondary" onClick={() => setCreated(null)}>
              Done
            </Button>
          </div>
        </div>
      )}
      {keys.isPending ? (
        <Skeleton className="h-32 rounded-xl" />
      ) : keys.isError ? (
        <ErrorNotice error={keys.error} onRetry={() => keys.refetch()} />
      ) : keys.data.length === 0 ? (
        <EmptyState icon={KeyRound} title="No API keys yet">
          Create one for each app that should start calls.
        </EmptyState>
      ) : (
        <ul className="divide-y divide-line rounded-xl border border-line">
          {keys.data.map((k) => (
            <KeyRow key={k.id} apiKey={k} />
          ))}
        </ul>
      )}
    </div>
  );
}
