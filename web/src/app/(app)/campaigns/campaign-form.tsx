"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Button, ErrorNotice, Field, Input, Select } from "@/components/ui";
import {
  api,
  type AgentSummary,
  type CampaignInput,
  type PhoneNumber,
} from "@/lib/api";
import { cn } from "@/lib/cn";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export const NEW_CAMPAIGN: Omit<
  CampaignInput,
  "agent_id" | "phone_number_ids"
> = {
  name: "",
  days: [0, 1, 2, 3, 4, 5],
  start_time: "08:00",
  end_time: "19:00",
  max_concurrent: 3,
  max_attempts: 3,
  retry_after_minutes: 60,
};

/** Name, agent, caller IDs, calling hours and retry rules of a campaign. */
export function CampaignForm({
  initial,
  submitLabel,
  pending,
  error,
  onSubmit,
  onCancel,
}: {
  initial: Partial<CampaignInput>;
  submitLabel: string;
  pending: boolean;
  error: unknown;
  onSubmit: (body: CampaignInput) => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<CampaignInput>({
    ...NEW_CAMPAIGN,
    agent_id: "",
    phone_number_ids: [],
    ...initial,
  });
  const set = (change: Partial<CampaignInput>) =>
    setForm((f) => ({ ...f, ...change }));
  const agents = useQuery({
    queryKey: ["agents", "active"],
    queryFn: () => api<AgentSummary[]>("/agents"),
  });
  const numbers = useQuery({
    queryKey: ["numbers"],
    queryFn: () => api<PhoneNumber[]>("/numbers"),
  });
  const outsideFairHours = form.start_time < "08:00" || form.end_time > "19:00";

  return (
    <form
      className="flex flex-col gap-5"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit(form);
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Campaign name">
          {(p) => (
            <Input
              {...p}
              required
              maxLength={120}
              placeholder="e.g. November interest – Karnataka"
              value={form.name}
              onChange={(e) => set({ name: e.target.value })}
            />
          )}
        </Field>
        <Field label="Agent" hint="Calls use the agent's published version.">
          {(p) => (
            <Select
              {...p}
              required
              value={form.agent_id}
              onChange={(e) => set({ agent_id: e.target.value })}
            >
              <option value="">Choose an agent…</option>
              {agents.data
                ?.filter((a) => a.status !== "archived")
                .map((a) => (
                  <option
                    key={a.id}
                    value={a.id}
                    disabled={a.published_version === null}
                  >
                    {a.name}
                    {a.published_version === null ? " (not published)" : ""}
                  </option>
                ))}
            </Select>
          )}
        </Field>
      </div>

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1.5 text-[13px] font-medium">Call from</legend>
        {numbers.data?.length === 0 && (
          <p className="text-[13px] text-muted">
            Add a phone number on the Numbers page first.
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          {numbers.data?.map((n) => {
            const on = form.phone_number_ids.includes(n.id);
            return (
              <label
                key={n.id}
                className={cn(
                  "flex cursor-pointer items-center gap-2 rounded-full border px-3 py-1.5 text-[13px]",
                  on ? "border-ink bg-surface" : "border-line hover:bg-surface",
                  !n.is_active && "opacity-50",
                )}
              >
                <input
                  type="checkbox"
                  className="sr-only"
                  checked={on}
                  disabled={!n.is_active}
                  onChange={() =>
                    set({
                      phone_number_ids: on
                        ? form.phone_number_ids.filter((id) => id !== n.id)
                        : [...form.phone_number_ids, n.id],
                    })
                  }
                />
                <span className="tabular">{n.e164}</span>
                <span className="text-muted">
                  {n.label || n.provider_account_name}
                </span>
              </label>
            );
          })}
        </div>
        <p className="text-2xs text-muted">
          Pick several to spread calls; retries use the next number.
        </p>
      </fieldset>

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1.5 text-[13px] font-medium">
          Calling hours (India time)
        </legend>
        <div className="flex flex-wrap items-center gap-2">
          {DAYS.map((day, i) => {
            const on = form.days.includes(i);
            return (
              <button
                key={day}
                type="button"
                aria-pressed={on}
                onClick={() =>
                  set({
                    days: on
                      ? form.days.filter((d) => d !== i)
                      : [...form.days, i].sort(),
                  })
                }
                className={cn(
                  "h-8 w-12 rounded-full text-[13px]",
                  on
                    ? "bg-ink text-on-ink"
                    : "bg-surface text-muted hover:text-ink",
                )}
              >
                {day}
              </button>
            );
          })}
          <span className="mx-1 text-muted">from</span>
          <Input
            aria-label="Start time"
            type="time"
            className="w-32"
            value={form.start_time}
            onChange={(e) => set({ start_time: e.target.value })}
          />
          <span className="text-muted">to</span>
          <Input
            aria-label="End time"
            type="time"
            className="w-32"
            value={form.end_time}
            onChange={(e) => set({ end_time: e.target.value })}
          />
        </div>
        {outsideFairHours && (
          <p className="text-2xs text-warning">
            RBI fair practices allow collection calls only between 8 AM and 7
            PM.
          </p>
        )}
      </fieldset>

      <div className="grid gap-4 sm:grid-cols-3">
        <Field
          label="Calls at the same time"
          hint="1–50, within your lines' limit."
        >
          {(p) => (
            <Input
              {...p}
              type="number"
              min={1}
              max={50}
              value={form.max_concurrent}
              onChange={(e) => set({ max_concurrent: Number(e.target.value) })}
            />
          )}
        </Field>
        <Field
          label="Attempts per contact"
          hint="Unanswered or busy calls are retried."
        >
          {(p) => (
            <Input
              {...p}
              type="number"
              min={1}
              max={10}
              value={form.max_attempts}
              onChange={(e) => set({ max_attempts: Number(e.target.value) })}
            />
          )}
        </Field>
        <Field label="Retry after (minutes)">
          {(p) => (
            <Input
              {...p}
              type="number"
              min={5}
              max={1440}
              value={form.retry_after_minutes}
              onChange={(e) =>
                set({ retry_after_minutes: Number(e.target.value) })
              }
            />
          )}
        </Field>
      </div>

      {Boolean(error) && <ErrorNotice error={error} />}
      <div className="flex gap-2">
        <Button
          type="submit"
          loading={pending}
          disabled={
            !form.agent_id ||
            form.phone_number_ids.length === 0 ||
            form.days.length === 0
          }
        >
          {submitLabel}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
