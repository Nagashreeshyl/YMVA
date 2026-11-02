"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Button, ErrorNotice, Field, Input, Panel, Select } from "@/components/ui";
import { api, type AgentDetail, type CallSummary, type PhoneNumber } from "@/lib/api";
import { CallDetailsFields } from "@/components/call-details-fields";
import { callDetails, rememberValues, rememberedValues } from "@/lib/variables";
import { can } from "@/lib/permissions";
import { useMe } from "@/lib/session";

export function CallPhone({ agent }: { agent: AgentDetail }) {
  const queryClient = useQueryClient();
  const numbers = useQuery({ queryKey: ["numbers"], queryFn: () => api<PhoneNumber[]>("/numbers") });
  const details = callDetails(agent.config);
  const [values, setValues] = useState<Record<string, string>>(() => rememberedValues(agent.id));
  // Like the browser test: unpublished edits can be tried on a real phone before publishing.
  const { data: me } = useMe();
  // Only people who edit the agent may call with unpublished edits.
  const canEdit = can(me, "agents.edit");
  const [useEdits, setUseEdits] = useState(canEdit && agent.has_unpublished_changes);
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
      description="Places a real call. Only call people who expect it."
    >
      <form
        className="grid max-w-3xl gap-4 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          rememberValues(agent.id, values);
          place.mutate({
            agent_id: agent.id,
            from_number_id: f.get("from"),
            to: f.get("to"),
            variables: Object.fromEntries(details.map((n) => [n, values[n] ?? ""])),
            use_edits: useEdits,
          });
        }}
      >
        {canEdit && agent.has_unpublished_changes && (
          <div className="sm:col-span-2">
            <Field
              label="Call with"
              hint={
                useEdits
                  ? "Your unpublished edits, so you can try them before publishing. Campaigns and incoming calls still use the published version."
                  : `The published version (v${agent.published_version}), as campaigns and incoming calls use. Your unpublished edits are not included.`
              }
            >
              {(p) => (
                <Select
                  {...p}
                  value={useEdits ? "edits" : "published"}
                  onChange={(e) => setUseEdits(e.target.value === "edits")}
                >
                  <option value="edits">Current edits (not yet published)</option>
                  <option value="published">Published version (v{agent.published_version})</option>
                </Select>
              )}
            </Field>
          </div>
        )}
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
        <CallDetailsFields config={agent.config} values={values} onChange={setValues} />
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
