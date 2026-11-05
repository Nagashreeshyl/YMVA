"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Plus, X } from "lucide-react";
import {
  Button,
  Field,
  Input,
  Panel,
  Segmented,
  Select,
  Switch,
  Textarea,
} from "@/components/ui";
import { VoicePicker } from "@/components/voice-picker";
import { detectVariables, variableLabel } from "@/lib/variables";
import {
  api,
  type AgentConfig,
  type AgentDetail,
  type AgentOptions,
  type ToolDef,
  type TrackedItem,
} from "@/lib/api";
import { cn } from "@/lib/cn";

type Updater = (draft: AgentConfig) => void;

const NO_TRANSFER = {
  enabled: false,
  to: "number" as const,
  phone: "",
  when: "The caller asks to speak to a person, or needs help you can't give",
};
// Same rule as the server: a full number with country code, or 10 Indian digits.
const isFullNumber = (v: string) =>
  /^(\+\d{11,15}|\d{10})$/.test(v.replace(/[^\d+]/g, ""));
export type SaveState = "saved" | "pending" | "saving" | "error";
const NO_RETRY = { enabled: false, max_retries: 2, wait_minutes: 30 };

const AUTOSAVE_DELAY_MS = 700;

export function AgentEditor({
  agent,
  options,
  readOnly,
  onSaveState,
}: {
  agent: AgentDetail;
  options: AgentOptions;
  readOnly: boolean;
  onSaveState: (state: SaveState, error?: string) => void;
}) {
  const queryClient = useQueryClient();
  const tools = useQuery({
    queryKey: ["tools"],
    queryFn: () => api<ToolDef[]>("/tools"),
  });
  // The editor owns its copy; it is re-created (via `key`) when the draft is replaced from outside.
  const [draft, setDraft] = useState<AgentConfig>(() =>
    structuredClone(agent.config),
  );
  const transfer = draft.transfer ?? NO_TRANSFER;
  const retry = draft.retry ?? NO_RETRY;
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const languageName = (code: string) =>
    options.languages.find((l) => l.code === code)?.name ?? code;
  const detected = detectVariables(draft);
  const [previewChoice, setPreviewLanguage] = useState<string | null>(null);
  const previewLanguage =
    previewChoice && draft.languages.includes(previewChoice)
      ? previewChoice
      : draft.default_language;

  const save = useMutation({
    mutationFn: (config: AgentConfig) =>
      api<AgentDetail>(`/agents/${agent.id}/draft`, {
        method: "PUT",
        json: { config },
      }),
    onMutate: () => onSaveState("saving"),
    onSuccess: (updated) => {
      queryClient.setQueryData(["agent", agent.id], updated);
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      onSaveState("saved");
    },
    onError: (error) =>
      onSaveState(
        "error",
        error instanceof Error ? error.message : "Couldn't save",
      ),
  });

  // Leaving the editor (e.g. switching tabs) saves any change still waiting for the timer.
  const pending = useRef<AgentConfig | null>(null);
  const { mutate } = save; // stable across renders
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
      if (pending.current) mutate(pending.current);
    },
    [mutate],
  );

  // Every change is saved as the draft shortly after you stop typing. No version is created.
  const update = (fn: Updater) =>
    setDraft((current) => {
      const next = structuredClone(current);
      fn(next);
      if (timer.current) clearTimeout(timer.current);
      onSaveState("pending");
      pending.current = next;
      timer.current = setTimeout(() => {
        pending.current = null;
        save.mutate(next);
      }, AUTOSAVE_DELAY_MS);
      return next;
    });

  const toggleLanguage = (code: string) =>
    update((d) => {
      if (d.languages.includes(code)) {
        if (d.languages.length === 1) return;
        d.languages = d.languages.filter((c) => c !== code);
        delete d.greetings[code];
        delete d.announcement.translations[code];
        if (d.default_language === code) d.default_language = d.languages[0];
      } else {
        d.languages.push(code);
      }
    });

  return (
    <fieldset disabled={readOnly} className="flex min-w-0 flex-col gap-6">
      <Panel
        title="How it talks"
        description="Standard uses Sarvam to hear and speak Indian languages and Gemini to think. Gemini Live does everything in one model with very natural voices. Both replied in about 1.3 s in our latest tests; Gemini Live sometimes mishears Kannada, so test both with your customers."
      >
        <div className="flex flex-col gap-6">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
            <Segmented
              label="Voice engine"
              value={draft.mode}
              onChange={(mode) => update((d) => void (d.mode = mode))}
              options={[
                { value: "pipeline", label: "Standard (Sarvam + Gemini)" },
                { value: "gemini_live", label: "Gemini Live" },
              ]}
            />
            <Segmented
              label="Speaking style"
              value={draft.speaking_style}
              onChange={(style) =>
                update((d) => void (d.speaking_style = style))
              }
              options={[
                {
                  value: "everyday_mix",
                  label: "Everyday mix (Kanglish, Tenglish…)",
                },
                { value: "pure", label: "Pure language" },
              ]}
            />
          </div>

          <div className="flex flex-wrap items-center gap-2 text-[13px]">
            <span className="text-muted">Preview in</span>
            {draft.languages.map((code) => (
              <button
                key={code}
                type="button"
                aria-pressed={previewLanguage === code}
                onClick={() => setPreviewLanguage(code)}
                className={
                  previewLanguage === code
                    ? "h-7 rounded-full bg-ink px-3 text-on-ink"
                    : "h-7 rounded-full px-3 text-muted hover:bg-surface hover:text-ink"
                }
              >
                {languageName(code)}
              </button>
            ))}
          </div>
          <Field
            label="Voice"
            hint={`Press play to hear each voice speak ${languageName(previewLanguage)} the way this agent would.`}
          >
            {() =>
              draft.mode === "pipeline" ? (
                <VoicePicker
                  engine="sarvam"
                  voices={options.sarvam_voices}
                  value={draft.pipeline.voice}
                  language={previewLanguage}
                  disabled={readOnly}
                  onChange={(v) => update((d) => void (d.pipeline.voice = v))}
                />
              ) : (
                <VoicePicker
                  engine="gemini"
                  voices={options.gemini_live_voices}
                  value={draft.gemini_live.voice}
                  language={previewLanguage}
                  disabled={readOnly}
                  onChange={(v) =>
                    update((d) => void (d.gemini_live.voice = v))
                  }
                />
              )
            }
          </Field>

          {draft.mode === "pipeline" ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Field label="Conversation model">
                {(p) => (
                  <Select
                    {...p}
                    value={draft.pipeline.llm_model}
                    onChange={(e) =>
                      update(
                        (d) => void (d.pipeline.llm_model = e.target.value),
                      )
                    }
                  >
                    {[
                      ...new Set([
                        draft.pipeline.llm_model,
                        ...options.llm_models,
                      ]),
                    ].map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field
                label="Backup model"
                hint="Takes over if the main model fails."
              >
                {(p) => (
                  <Select
                    {...p}
                    value={draft.pipeline.backup_llm_model}
                    onChange={(e) =>
                      update(
                        (d) =>
                          void (d.pipeline.backup_llm_model = e.target.value),
                      )
                    }
                  >
                    {[
                      ...new Set([
                        draft.pipeline.backup_llm_model,
                        ...options.llm_models,
                      ]),
                    ].map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field
                label={`Speaking pace · ${draft.pipeline.speaking_pace.toFixed(1)}×`}
              >
                {(p) => (
                  <input
                    {...p}
                    type="range"
                    min={0.6}
                    max={1.6}
                    step={0.1}
                    value={draft.pipeline.speaking_pace}
                    onChange={(e) =>
                      update(
                        (d) =>
                          void (d.pipeline.speaking_pace = Number(
                            e.target.value,
                          )),
                      )
                    }
                    className="h-10 accent-[var(--ink)]"
                  />
                )}
              </Field>
              <Field
                label={`Creativity · ${draft.pipeline.temperature.toFixed(1)}`}
                hint="Lower is more predictable."
              >
                {(p) => (
                  <input
                    {...p}
                    type="range"
                    min={0}
                    max={1.2}
                    step={0.1}
                    value={draft.pipeline.temperature}
                    onChange={(e) =>
                      update(
                        (d) =>
                          void (d.pipeline.temperature = Number(
                            e.target.value,
                          )),
                      )
                    }
                    className="h-10 accent-[var(--ink)]"
                  />
                )}
              </Field>
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Gemini Live model">
                {(p) => (
                  <Select
                    {...p}
                    value={draft.gemini_live.model}
                    onChange={(e) =>
                      update((d) => void (d.gemini_live.model = e.target.value))
                    }
                  >
                    {[
                      ...new Set([
                        draft.gemini_live.model,
                        ...options.gemini_live_models,
                      ]),
                    ].map((m) => (
                      <option key={m} value={m}>
                        {m.replace("models/", "")}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field
                label={`Creativity · ${draft.gemini_live.temperature.toFixed(1)}`}
                hint="Lower is more predictable; higher sounds livelier."
              >
                {(p) => (
                  <input
                    {...p}
                    type="range"
                    min={0}
                    max={1.5}
                    step={0.1}
                    value={draft.gemini_live.temperature}
                    onChange={(e) =>
                      update(
                        (d) =>
                          void (d.gemini_live.temperature = Number(
                            e.target.value,
                          )),
                      )
                    }
                    className="h-10 accent-[var(--ink)]"
                  />
                )}
              </Field>
            </div>
          )}
        </div>
      </Panel>

      <Panel
        title="Instructions"
        description="Who the agent is, what it must achieve, and every rule it must follow. Use {{variable}} to insert customer details."
      >
        <Textarea
          aria-label="Agent instructions"
          rows={18}
          value={draft.system_prompt}
          onChange={(e) =>
            update((d) => void (d.system_prompt = e.target.value))
          }
          className="font-[450]"
        />
      </Panel>

      <Panel
        title="Languages"
        description="The agent opens in the customer's preferred language (or their state's), then follows whatever language they speak, unless you keep it to one below."
      >
        <div className="flex flex-col gap-5">
          <div
            className="flex flex-wrap gap-2"
            role="group"
            aria-label="Languages the agent speaks"
          >
            {options.languages.map((l) => {
              const on = draft.languages.includes(l.code);
              return (
                <button
                  key={l.code}
                  type="button"
                  aria-pressed={on}
                  onClick={() => toggleLanguage(l.code)}
                  className={cn(
                    "h-8 rounded-full border px-3.5 text-[13px] transition-colors duration-150",
                    on
                      ? "border-ink bg-ink text-on-ink"
                      : "border-line-strong text-muted hover:text-ink",
                  )}
                >
                  {l.name}{" "}
                  {l.native !== l.name && (
                    <span className={on ? "opacity-70" : "text-faint"}>
                      {l.native}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          <Switch
            label="Follow the caller's language"
            description={
              draft.follow_caller_language === false
                ? "Off: the whole call stays in the opening language, whatever the caller speaks."
                : "When the caller switches language (say Tamil to Kannada), the agent switches with them. If they ask for a language, it keeps to that one."
            }
            checked={draft.follow_caller_language !== false}
            onChange={(on) =>
              update((d) => void (d.follow_caller_language = on))
            }
          />
          <div className="max-w-xs">
            <Field
              label="Default opening language"
              hint="Used when the customer's language and state are unknown."
            >
              {(p) => (
                <Select
                  {...p}
                  value={draft.default_language}
                  onChange={(e) =>
                    update((d) => void (d.default_language = e.target.value))
                  }
                >
                  {draft.languages.map((c) => (
                    <option key={c} value={c}>
                      {languageName(c)}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          </div>
          <div className="flex flex-col gap-3">
            <h3 className="text-[13px] font-medium">
              Opening line per language
            </h3>
            <p className="-mt-2 text-2xs text-muted">
              Leave one empty and the agent will write its own greeting in that
              language.
            </p>
            {draft.languages.map((code) => (
              <Field key={code} label={languageName(code)}>
                {(p) => (
                  <Textarea
                    {...p}
                    rows={2}
                    className="min-h-0"
                    value={draft.greetings[code] ?? ""}
                    onChange={(e) =>
                      update((d) => {
                        if (e.target.value) d.greetings[code] = e.target.value;
                        else delete d.greetings[code];
                      })
                    }
                  />
                )}
              </Field>
            ))}
          </div>
        </div>
      </Panel>

      <Panel title="Recording announcement">
        <div className="flex flex-col gap-4">
          <Switch
            label="Play an announcement at the start of every call"
            description="For example, that the call may be recorded."
            checked={draft.announcement.enabled}
            onChange={(enabled) =>
              update((d) => void (d.announcement.enabled = enabled))
            }
          />
          {draft.announcement.enabled && (
            <>
              <Field label="Announcement (English)">
                {(p) => (
                  <Textarea
                    {...p}
                    rows={2}
                    className="min-h-0"
                    value={draft.announcement.text}
                    onChange={(e) =>
                      update((d) => void (d.announcement.text = e.target.value))
                    }
                  />
                )}
              </Field>
              {draft.languages
                .filter((c) => c !== "en")
                .map((code) => (
                  <Field
                    key={code}
                    label={`${languageName(code)} version`}
                    hint="Optional. Without it, the English text is played."
                  >
                    {(p) => (
                      <Textarea
                        {...p}
                        rows={2}
                        className="min-h-0"
                        value={draft.announcement.translations[code] ?? ""}
                        onChange={(e) =>
                          update((d) => {
                            if (e.target.value)
                              d.announcement.translations[code] =
                                e.target.value;
                            else delete d.announcement.translations[code];
                          })
                        }
                      />
                    )}
                  </Field>
                ))}
            </>
          )}
        </div>
      </Panel>

      <Panel title="Customer's branch">
        <Switch
          label="Every call must include the customer's branch"
          description="Calls without a branch listed in Settings → Branches are refused before the phone rings. The branch's state picks the opening language (Karnataka → Kannada, Andhra → Telugu, Tamil Nadu → Tamil) and its manager takes transfers."
          checked={draft.branch_required ?? false}
          onChange={(on) => update((d) => void (d.branch_required = on))}
        />
      </Panel>

      <Panel title="Transfer to a person">
        <div className="flex flex-col gap-4">
          <Switch
            label="Let the agent transfer the call to a colleague"
            description="When the caller asks for a person, the agent says it's connecting them and the call rings this number. Works on phone calls (not browser tests or Exotel)."
            checked={transfer.enabled}
            onChange={(enabled) =>
              update((d) => void (d.transfer = { ...transfer, enabled }))
            }
          />
          {transfer.enabled && (
            <>
              <Segmented
                label="Transfer to"
                value={transfer.to ?? "number"}
                onChange={(to) =>
                  update((d) => void (d.transfer = { ...transfer, to }))
                }
                options={[
                  { value: "number", label: "A fixed number" },
                  {
                    value: "branch_manager",
                    label: "The customer's branch manager",
                  },
                ]}
              />
              {transfer.to === "branch_manager" && (
                <p className="text-[13px] text-muted">
                  Each call then needs the customer&apos;s{" "}
                  <strong className="text-ink">branch</strong> (where they took
                  the loan), and the call goes to that branch&apos;s manager
                  from{" "}
                  <Link
                    href="/settings"
                    className="underline underline-offset-4"
                  >
                    Settings → Branches
                  </Link>
                  . Customers whose branch isn&apos;t listed go to the number
                  below.
                </p>
              )}
              <Field
                label={
                  transfer.to === "branch_manager"
                    ? "Number when the branch isn't listed"
                    : "Number to transfer to"
                }
                hint={
                  transfer.phone && !isFullNumber(transfer.phone)
                    ? "Enter the full number; until then the agent can't transfer."
                    : transfer.to === "branch_manager"
                      ? "Optional, e.g. the head office. Leave empty and those calls aren't transferred."
                      : "With country code, e.g. +91 80 1234 5678."
                }
              >
                {(p) => (
                  <Input
                    {...p}
                    type="tel"
                    inputMode="tel"
                    placeholder="+91 80 1234 5678"
                    value={transfer.phone}
                    onChange={(e) =>
                      update(
                        (d) =>
                          void (d.transfer = {
                            ...transfer,
                            phone: e.target.value,
                          }),
                      )
                    }
                  />
                )}
              </Field>
              <Field
                label="When to transfer"
                hint="In plain words. The agent follows this."
              >
                {(p) => (
                  <Textarea
                    {...p}
                    rows={2}
                    className="min-h-0"
                    value={transfer.when}
                    onChange={(e) =>
                      update(
                        (d) =>
                          void (d.transfer = {
                            ...transfer,
                            when: e.target.value,
                          }),
                      )
                    }
                  />
                )}
              </Field>
            </>
          )}
        </div>
      </Panel>

      <Panel
        title="Customer details"
        description="Write {{name}} anywhere in the instructions or greetings, e.g. {{customer_name}} or {{due_amount}}. Each one becomes a field you fill in when placing a call, or that a campaign fills from your sheet."
      >
        {detected.length === 0 ? (
          <p className="text-[13px] text-muted">
            No customer details used yet. Add one by typing it in double braces,
            like {"{{customer_name}}"}.
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            {detected.map((name) => (
              <li
                key={name}
                className="grid gap-2 sm:grid-cols-[220px_1fr] sm:items-center"
              >
                <code className="w-fit rounded-lg bg-surface px-2.5 py-1.5 font-mono text-[13px]">{`{{${name}}}`}</code>
                <Input
                  aria-label={`Note for ${name}`}
                  placeholder={`What is ${variableLabel(name).toLowerCase()}? (optional, shown on the call form)`}
                  value={
                    draft.variables.find((v) => v.name === name)?.description ??
                    ""
                  }
                  onChange={(e) =>
                    update((d) => {
                      const existing = d.variables.find((v) => v.name === name);
                      if (existing) existing.description = e.target.value;
                      else
                        d.variables.push({
                          name,
                          description: e.target.value,
                          example: "",
                        });
                    })
                  }
                />
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel
        title="After the call"
        description="When a call ends, its transcript is read and a short summary, one outcome and these details are saved on the call. Outcomes are counted on the Overview."
      >
        <div className="flex flex-col gap-6">
          <TrackedList
            label="Outcomes"
            hint="Each call gets the one that fits best, or Other."
            items={draft.analysis.outcomes}
            namePlaceholder="e.g. Promised to pay"
            descriptionPlaceholder="When it applies, e.g. Gave a date to pay"
            onChange={(items) =>
              update((d) => void (d.analysis.outcomes = items))
            }
          />
          <TrackedList
            label="Details to write down"
            hint="Saved only when the customer actually says them."
            items={draft.analysis.details}
            namePlaceholder="e.g. Promised payment date"
            descriptionPlaceholder="What to write, e.g. The date, as YYYY-MM-DD"
            onChange={(items) =>
              update((d) => void (d.analysis.details = items))
            }
          />
          <div className="flex flex-col gap-2">
            <div>
              <h3 className="text-[13px] font-medium">Send the results to</h3>
              <p className="text-2xs text-muted">
                After each call, these tools get the results. They can use{" "}
                {"{{summary}}"}, {"{{outcome}}"}, {"{{action_items}}"},{" "}
                {"{{call_id}}"}, {"{{attempt}}"}, {"{{branch}}"},{" "}
                {"{{duration_seconds}}"}, {"{{call_time}}"}, the customer
                details and each detail above
                {draft.analysis.details.length > 0 &&
                  ` (${draft.analysis.details
                    .map(
                      (d) =>
                        `{{${d.name
                          .toLowerCase()
                          .replace(/[^a-z0-9]+/g, "_")
                          .replace(/^_|_$/g, "")}}}`,
                    )
                    .join(", ")})`}
                .
              </p>
            </div>
            {(tools.data ?? []).length === 0 ? (
              <p className="text-[13px] text-muted">
                No tools yet.{" "}
                <Link
                  href="/tools"
                  className="underline underline-offset-4 hover:text-ink"
                >
                  Create one on the Tools page
                </Link>
                , e.g. one that adds a row to your results sheet.
              </p>
            ) : (
              tools.data?.map((t) => (
                <label
                  key={t.id}
                  className="flex items-center gap-3 text-[13px]"
                >
                  <input
                    type="checkbox"
                    checked={draft.analysis.send_to.includes(t.name)}
                    onChange={(e) =>
                      update((d) => {
                        d.analysis.send_to = e.target.checked
                          ? [...d.analysis.send_to, t.name]
                          : d.analysis.send_to.filter((n) => n !== t.name);
                      })
                    }
                  />
                  <span className="font-mono font-medium">{t.name}</span>
                  <span className="truncate text-muted">{t.description}</span>
                </label>
              ))
            )}
          </div>
        </div>
      </Panel>

      <Panel
        title="Tools"
        description="Actions this agent can take during a call, such as saving a promised payment date to your sheet. Set tools up on the Tools page."
      >
        {(tools.data ?? []).length === 0 ? (
          <p className="text-[13px] text-muted">
            No tools yet.{" "}
            <Link
              href="/tools"
              className="underline underline-offset-4 hover:text-ink"
            >
              Create one on the Tools page
            </Link>
            .
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            {tools.data?.map((t) => (
              <li key={t.id}>
                <label className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={draft.tools.includes(t.name)}
                    onChange={(e) =>
                      update((d) => {
                        d.tools = e.target.checked
                          ? [...d.tools, t.name]
                          : d.tools.filter((n) => n !== t.name);
                      })
                    }
                  />
                  <span>
                    <span className="font-mono text-[13px] font-medium">
                      {t.name}
                    </span>
                    {!t.is_active && (
                      <span className="text-2xs text-muted">
                        {" "}
                        · switched off
                      </span>
                    )}
                    <span className="block text-[13px] text-muted">
                      {t.description}
                    </span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel
        title="Call atmosphere"
        description="Sounds are added only where a real call would have them."
      >
        <div className="flex max-w-xl flex-col gap-4">
          <Switch
            label="Office background"
            description="A real call-centre murmur under the whole call, so it sounds like the agent is in an office."
            checked={draft.atmosphere.room_tone}
            onChange={(on) => update((d) => void (d.atmosphere.room_tone = on))}
          />
          <Switch
            label="Typing while using a tool"
            description="Faint keyboard sounds only while the agent looks up or saves details, so the pause sounds like someone working."
            checked={draft.atmosphere.typing_while_thinking}
            onChange={(on) =>
              update((d) => void (d.atmosphere.typing_while_thinking = on))
            }
          />
          <Field
            label={`Volume · ${Math.round(draft.atmosphere.volume * 100)}%`}
          >
            {(p) => (
              <input
                {...p}
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={draft.atmosphere.volume}
                onChange={(e) =>
                  update(
                    (d) => void (d.atmosphere.volume = Number(e.target.value)),
                  )
                }
                className="h-10 accent-[var(--ink)]"
              />
            )}
          </Field>
        </div>
      </Panel>

      <Panel title="Retry unanswered calls">
        <div className="flex flex-col gap-4">
          <Switch
            label="Call again when the customer isn't reached"
            description="No answer, busy, switched off, voicemail or nobody speaking: the agent hangs up and calls the number again later. Campaigns use their own retry settings."
            checked={retry.enabled}
            onChange={(enabled) =>
              update((d) => void (d.retry = { ...retry, enabled }))
            }
          />
          {retry.enabled && (
            <div className="grid max-w-xl gap-4 sm:grid-cols-2">
              <Field
                label="Retries"
                hint="Extra calls after the first, 1 to 10."
              >
                {(p) => (
                  <Input
                    {...p}
                    type="number"
                    min={1}
                    max={10}
                    value={retry.max_retries}
                    onChange={(e) =>
                      update(
                        (d) =>
                          void (d.retry = {
                            ...retry,
                            max_retries: Math.min(
                              10,
                              Math.max(1, Number(e.target.value) || 1),
                            ),
                          }),
                      )
                    }
                  />
                )}
              </Field>
              <Field
                label="Wait between calls (minutes)"
                hint="5 to 1440 (a day)."
              >
                {(p) => (
                  <Input
                    {...p}
                    type="number"
                    min={5}
                    max={1440}
                    value={retry.wait_minutes}
                    onChange={(e) =>
                      update(
                        (d) =>
                          void (d.retry = {
                            ...retry,
                            wait_minutes: Math.min(
                              1440,
                              Math.max(5, Number(e.target.value) || 5),
                            ),
                          }),
                      )
                    }
                  />
                )}
              </Field>
            </div>
          )}
        </div>
      </Panel>

      <Panel title="Call limits">
        <div className="grid max-w-xl gap-4 sm:grid-cols-2">
          <Field label="Longest call (minutes)">
            {(p) => (
              <Input
                {...p}
                type="number"
                min={1}
                max={60}
                value={Math.round(draft.limits.max_duration_seconds / 60)}
                onChange={(e) =>
                  update(
                    (d) =>
                      void (d.limits.max_duration_seconds =
                        Math.max(1, Number(e.target.value)) * 60),
                  )
                }
              />
            )}
          </Field>
          <Field
            label="Hang up after silence (seconds)"
            hint="The agent checks once, then ends the call."
          >
            {(p) => (
              <Input
                {...p}
                type="number"
                min={5}
                max={120}
                value={draft.limits.silence_timeout_seconds}
                onChange={(e) =>
                  update(
                    (d) =>
                      void (d.limits.silence_timeout_seconds = Number(
                        e.target.value,
                      )),
                  )
                }
              />
            )}
          </Field>
        </div>
      </Panel>
    </fieldset>
  );
}

function TrackedList({
  label,
  hint,
  items,
  namePlaceholder,
  descriptionPlaceholder,
  onChange,
}: {
  label: string;
  hint: string;
  items: TrackedItem[];
  namePlaceholder: string;
  descriptionPlaceholder: string;
  onChange: (items: TrackedItem[]) => void;
}) {
  const edit = (i: number, change: Partial<TrackedItem>) =>
    onChange(items.map((item, j) => (j === i ? { ...item, ...change } : item)));
  return (
    <div className="flex flex-col gap-2">
      <div>
        <h3 className="text-[13px] font-medium">{label}</h3>
        <p className="text-2xs text-muted">{hint}</p>
      </div>
      {items.map((item, i) => (
        <div
          key={i}
          className="grid gap-2 sm:grid-cols-[220px_1fr_auto] sm:items-center"
        >
          <Input
            aria-label={`${label} ${i + 1}: name`}
            maxLength={60}
            placeholder={namePlaceholder}
            value={item.name}
            onChange={(e) => edit(i, { name: e.target.value })}
          />
          <Input
            aria-label={`${label} ${i + 1}: when it applies`}
            maxLength={300}
            placeholder={descriptionPlaceholder}
            value={item.description}
            onChange={(e) => edit(i, { description: e.target.value })}
          />
          <Button
            variant="ghost"
            size="sm"
            aria-label={`Remove ${item.name || `${label.toLowerCase()} ${i + 1}`}`}
            onClick={() => onChange(items.filter((_, j) => j !== i))}
          >
            <X className="size-4" aria-hidden />
          </Button>
        </div>
      ))}
      {items.length < 20 && (
        <Button
          variant="secondary"
          size="sm"
          className="w-fit"
          onClick={() => onChange([...items, { name: "", description: "" }])}
        >
          <Plus className="size-3.5" aria-hidden /> Add
        </Button>
      )}
    </div>
  );
}
