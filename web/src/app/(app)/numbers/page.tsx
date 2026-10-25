"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Phone, Plus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import {
  Badge,
  Button,
  EmptyState,
  ErrorNotice,
  Field,
  Input,
  PageHeader,
  Select,
  Skeleton,
} from "@/components/ui";
import { api, type AgentSummary, type Integrations, type PhoneNumber } from "@/lib/api";
import { useMe } from "@/lib/session";

function AddNumberForm({
  integrations,
  agents,
  onDone,
}: {
  integrations: Integrations;
  agents: AgentSummary[];
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const add = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<PhoneNumber>("/numbers", { method: "POST", json: body }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["numbers"] });
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
          e164: f.get("e164"),
          label: f.get("label"),
          provider_account_id: f.get("account"),
          inbound_agent_id: f.get("agent") || null,
        });
      }}
    >
      <h2 className="text-[15px] font-semibold sm:col-span-2">Add a phone number</h2>
      <Field label="Phone number" hint="With country code, e.g. +91 80 1234 5678">
        {(p) => <Input {...p} name="e164" required inputMode="tel" autoFocus />}
      </Field>
      <Field label="Label" hint="Optional, e.g. “Reminders – Bengaluru”">
        {(p) => <Input {...p} name="label" />}
      </Field>
      <Field label="Telephony account">
        {(p) => (
          <Select {...p} name="account" required>
            {integrations.telephony.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name} ({a.provider})
              </option>
            ))}
          </Select>
        )}
      </Field>
      <Field label="Answers incoming calls" hint="The published version of this agent answers.">
        {(p) => (
          <Select {...p} name="agent" defaultValue="">
            <option value="">No one (outbound only)</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </Select>
        )}
      </Field>
      {add.isError && (
        <div className="sm:col-span-2">
          <ErrorNotice error={add.error} />
        </div>
      )}
      <div className="flex gap-2 sm:col-span-2">
        <Button type="submit" loading={add.isPending}>
          Add number
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function NumberRow({
  number,
  agents,
  canEdit,
}: {
  number: PhoneNumber;
  agents: AgentSummary[];
  canEdit: boolean;
}) {
  const queryClient = useQueryClient();
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["numbers"] });
  const update = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api(`/numbers/${number.id}`, { method: "PATCH", json: body }),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: () => api(`/numbers/${number.id}`, { method: "DELETE" }),
    onSuccess: refresh,
  });

  return (
    <li className="flex flex-col gap-2 px-5 py-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <p className="tabular font-medium">{number.e164}</p>
          <p className="text-[13px] text-muted">
            {number.label ? `${number.label} · ` : ""}
            {number.provider_account_name}
          </p>
        </div>
        {!number.is_active && <Badge tone="danger">Off</Badge>}
        {canEdit ? (
          <Select
            aria-label={`Agent answering ${number.e164}`}
            className="h-8 w-56 text-[13px]"
            value={number.inbound_agent_id ?? ""}
            disabled={update.isPending}
            onChange={(e) =>
              update.mutate(
                e.target.value ? { inbound_agent_id: e.target.value } : { clear_inbound_agent: true },
              )
            }
          >
            <option value="">No inbound agent</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                Answered by {a.name}
              </option>
            ))}
          </Select>
        ) : (
          <span className="text-[13px] text-muted">
            {number.inbound_agent_name ? `Answered by ${number.inbound_agent_name}` : "Outbound only"}
          </span>
        )}
        {canEdit && (
          <Button
            variant="danger"
            size="sm"
            loading={remove.isPending}
            onClick={() =>
              window.confirm(`Remove ${number.e164} from YMVA? (It stays in your provider account.)`) &&
              remove.mutate()
            }
          >
            Remove
          </Button>
        )}
      </div>
      {(update.isError || remove.isError) && <ErrorNotice error={update.error ?? remove.error} />}
    </li>
  );
}

export default function NumbersPage() {
  const { data: me } = useMe();
  const canEdit = me?.role === "admin";
  const [adding, setAdding] = useState(false);
  const numbers = useQuery({ queryKey: ["numbers"], queryFn: () => api<PhoneNumber[]>("/numbers") });
  const agents = useQuery({ queryKey: ["agents"], queryFn: () => api<AgentSummary[]>("/agents") });
  const integrations = useQuery({
    queryKey: ["integrations"],
    queryFn: () => api<Integrations>("/integrations"),
  });
  const noAccounts = integrations.data && integrations.data.telephony.length === 0;

  return (
    <>
      <PageHeader
        title="Numbers"
        description="Your phone numbers, and which agent answers each one."
        actions={
          canEdit && !adding && !noAccounts ? (
            <Button onClick={() => setAdding(true)}>
              <Plus className="size-4" aria-hidden /> Add number
            </Button>
          ) : undefined
        }
      />
      {numbers.isPending || agents.isPending || integrations.isPending ? (
        <Skeleton className="h-40 rounded-xl" />
      ) : numbers.isError ? (
        <ErrorNotice error={numbers.error} onRetry={() => numbers.refetch()} />
      ) : (
        <div className="flex flex-col gap-6">
          {adding && integrations.data && agents.data && (
            <AddNumberForm
              integrations={integrations.data}
              agents={agents.data}
              onDone={() => setAdding(false)}
            />
          )}
          {numbers.data.length === 0 && !adding ? (
            <EmptyState
              icon={Phone}
              title="No numbers yet"
              action={
                noAccounts ? (
                  <Link href="/integrations" className="text-[13px] font-medium underline underline-offset-4">
                    Connect a telephony account first
                  </Link>
                ) : undefined
              }
            >
              Add the numbers you bought from Plivo or Vobiz. Each can make outbound calls, and an agent can
              answer calls to it.
            </EmptyState>
          ) : (
            numbers.data.length > 0 && (
              <ul className="divide-y divide-line rounded-xl border border-line">
                {numbers.data.map((n) => (
                  <NumberRow key={n.id} number={n} agents={agents.data ?? []} canEdit={canEdit} />
                ))}
              </ul>
            )
          )}
        </div>
      )}
    </>
  );
}
