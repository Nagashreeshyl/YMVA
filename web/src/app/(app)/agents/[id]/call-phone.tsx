"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Button, ErrorNotice, Field, Input, Panel, Select } from "@/components/ui";
import { api, type AgentDetail, type CallSummary, type PhoneNumber } from "@/lib/api";

export function CallPhone({ agent }: { agent: AgentDetail }) {
  const queryClient = useQueryClient();
  const numbers = useQuery({ queryKey: ["numbers"], queryFn: () => api<PhoneNumber[]>("/numbers") });
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(agent.config.variables.map((v) => [v.name, ""])),
  );
  const place = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<CallSummary>("/calls/outbound", { method: "POST", json: body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["calls"] }),
  });
  const active = (numbers.data ?? []).filter((n) => n.is_active);

  if (agent.published_version === null) {
    return (
      <Panel title="Call a phone">
        <p className="text-[13px] text-muted">
          Publish a version of this agent first. Real phone calls always use the published version.
        </p>
      </Panel>
    );
  }
  if (numbers.isSuccess && active.length === 0) {
    return (
      <Panel title="Call a phone">
        <p className="text-[13px] text-muted">
          Add a phone number in{" "}
          <Link href="/numbers" className="font-medium text-ink underline underline-offset-4">
            Numbers
          </Link>{" "}
          to call from.
        </p>
      </Panel>
    );
  }

  return (
    <Panel
      title="Call a phone"
      description={`Places a real call with the published version (v${agent.published_version}). Only call people who expect it.`}
    >
      <form
        className="grid max-w-3xl gap-4 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          place.mutate({
            agent_id: agent.id,
            from_number_id: f.get("from"),
            to: f.get("to"),
            variables: values,
          });
        }}
      >
        <Field label="Call from">
          {(p) => (
            <Select {...p} name="from" required>
              {active.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.e164}
                  {n.label ? ` · ${n.label}` : ""}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Phone number to call" hint="With country code, e.g. +91 98765 43210">
          {(p) => <Input {...p} name="to" required inputMode="tel" />}
        </Field>
        {agent.config.variables.map((v) => (
          <Field key={v.name} label={v.name} hint={v.description}>
            {(p) => (
              <Input
                {...p}
                value={values[v.name] ?? ""}
                placeholder={v.example}
                onChange={(e) => setValues((x) => ({ ...x, [v.name]: e.target.value }))}
              />
            )}
          </Field>
        ))}
        {place.isError && (
          <div className="sm:col-span-2">
            <ErrorNotice error={place.error} />
          </div>
        )}
        <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
          <Button type="submit" loading={place.isPending}>
            Place call
          </Button>
          {place.isSuccess && (
            <span className="text-[13px] text-muted">
              Ringing…{" "}
              <Link
                href={`/calls/${place.data.id}`}
                className="font-medium text-ink underline underline-offset-4"
              >
                Follow this call
              </Link>
            </span>
          )}
        </div>
      </form>
    </Panel>
  );
}
