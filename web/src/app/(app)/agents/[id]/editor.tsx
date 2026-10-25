"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button, Field, Input, Panel, Segmented, Select, Switch, Textarea } from "@/components/ui";
import { api, type AgentConfig, type AgentDetail, type AgentOptions } from "@/lib/api";
import { cn } from "@/lib/cn";

type Updater = (draft: AgentConfig) => void;
export type SaveState = "saved" | "pending" | "saving" | "error";

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
  // The editor owns its copy; it is re-created (via `key`) when the draft is replaced from outside.
  const [draft, setDraft] = useState<AgentConfig>(() => structuredClone(agent.config));
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const languageName = (code: string) => options.languages.find((l) => l.code === code)?.name ?? code;

  const save = useMutation({
    mutationFn: (config: AgentConfig) =>
      api<AgentDetail>(`/agents/${agent.id}/draft`, { method: "PUT", json: { config } }),
    onMutate: () => onSaveState("saving"),
    onSuccess: (updated) => {
      queryClient.setQueryData(["agent", agent.id], updated);
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      onSaveState("saved");
    },
    onError: (error) => onSaveState("error", error instanceof Error ? error.message : "Couldn't save"),
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
description="Standard (recommended) uses Sarvam to hear and speak Indian languages and Gemini to think: replies in about 1.3 s in our tests. Gemini Live does everything in one model but was slower (2–8 s) and misheard Kannada in testing."
      >
        <div className="flex flex-col gap-5">
          <Segmented
            label="Voice engine"
            value={draft.mode}
            onChange={(mode) => update((d) => void (d.mode = mode))}
            options={[
              { value: "pipeline", label: "Standard (Sarvam + Gemini)" },
              { value: "gemini_live", label: "Gemini Live" },
            ]}
          />
          {draft.mode === "pipeline" ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <Field label="Voice">
                {(p) => (
                  <Select
                    {...p}
                    value={draft.pipeline.voice}
                    onChange={(e) => update((d) => void (d.pipeline.voice = e.target.value))}
                  >
                    {options.sarvam_voices.map((v) => (
                      <option key={v} value={v}>
                        {v.charAt(0).toUpperCase() + v.slice(1)}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field label="Conversation model">
                {(p) => (
                  <Select
                    {...p}
                    value={draft.pipeline.llm_model}
                    onChange={(e) => update((d) => void (d.pipeline.llm_model = e.target.value))}
                  >
                    {[...new Set([draft.pipeline.llm_model, ...options.llm_models])].map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field label="Backup model" hint="Takes over automatically if the main model fails.">
                {(p) => (
                  <Select
                    {...p}
                    value={draft.pipeline.backup_llm_model}
                    onChange={(e) => update((d) => void (d.pipeline.backup_llm_model = e.target.value))}
                  >
                    {[...new Set([draft.pipeline.backup_llm_model, ...options.llm_models])].map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field label={`Speaking pace · ${draft.pipeline.speaking_pace.toFixed(1)}×`}>
                {(p) => (
                  <input
                    {...p}
                    type="range"
                    min={0.6}
                    max={1.6}
                    step={0.1}
                    value={draft.pipeline.speaking_pace}
                    onChange={(e) => update((d) => void (d.pipeline.speaking_pace = Number(e.target.value)))}
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
                    onChange={(e) => update((d) => void (d.pipeline.temperature = Number(e.target.value)))}
                    className="h-10 accent-[var(--ink)]"
                  />
                )}
              </Field>
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Voice">
                {(p) => (
                  <Select
                    {...p}
                    value={draft.gemini_live.voice}
                    onChange={(e) => update((d) => void (d.gemini_live.voice = e.target.value))}
                  >
                    {options.gemini_live_voices.map((v) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field label="Gemini Live model">
                {(p) => (
                  <Select
                    {...p}
                    value={draft.gemini_live.model}
                    onChange={(e) => update((d) => void (d.gemini_live.model = e.target.value))}
                  >
                    {[...new Set([draft.gemini_live.model, ...options.gemini_live_models])].map((m) => (
                      <option key={m} value={m}>
                        {m.replace("models/", "")}
                      </option>
                    ))}
                  </Select>
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
          onChange={(e) => update((d) => void (d.system_prompt = e.target.value))}
          className="font-[450]"
        />
      </Panel>

      <Panel
        title="Languages"
        description="The agent opens in the customer's preferred language (or their state's), then follows whatever language they speak."
      >
        <div className="flex flex-col gap-5">
          <div className="flex flex-wrap gap-2" role="group" aria-label="Languages the agent speaks">
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
                    on ? "border-ink bg-ink text-on-ink" : "border-line-strong text-muted hover:text-ink",
                  )}
                >
                  {l.name}{" "}
                  {l.native !== l.name && (
                    <span className={on ? "opacity-70" : "text-faint"}>{l.native}</span>
                  )}
                </button>
              );
            })}
          </div>
          <div className="max-w-xs">
            <Field
              label="Default opening language"
              hint="Used when the customer's language and state are unknown."
            >
              {(p) => (
                <Select
                  {...p}
                  value={draft.default_language}
                  onChange={(e) => update((d) => void (d.default_language = e.target.value))}
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
            <h3 className="text-[13px] font-medium">Opening line per language</h3>
            <p className="-mt-2 text-2xs text-muted">
              Leave one empty and the agent will write its own greeting in that language.
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
            onChange={(enabled) => update((d) => void (d.announcement.enabled = enabled))}
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
                    onChange={(e) => update((d) => void (d.announcement.text = e.target.value))}
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
                            if (e.target.value) d.announcement.translations[code] = e.target.value;
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

      <Panel
        title="Customer details"
        description="Values supplied for each call (from a campaign, your CRM or the call form) and usable as {{name}} in instructions and greetings."
        actions={
          !readOnly && (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => update((d) => void d.variables.push({ name: "", description: "", example: "" }))}
            >
              <Plus className="size-4" aria-hidden /> Add detail
            </Button>
          )
        }
      >
        {draft.variables.length === 0 ? (
          <p className="text-[13px] text-muted">No customer details defined.</p>
        ) : (
          <div className="flex flex-col gap-3">
            {draft.variables.map((v, i) => (
              <div key={i} className="grid gap-3 sm:grid-cols-[1fr_1.4fr_1fr_auto] sm:items-end">
                <Field label="Name">
                  {(p) => (
                    <Input
                      {...p}
                      value={v.name}
                      placeholder="customer_name"
                      pattern="[a-zA-Z_][a-zA-Z0-9_]*"
                      className="font-mono text-[13px]"
                      onChange={(e) =>
                        update((d) => void (d.variables[i].name = e.target.value.replace(/\s+/g, "_")))
                      }
                    />
                  )}
                </Field>
                <Field label="What it is">
                  {(p) => (
                    <Input
                      {...p}
                      value={v.description}
                      onChange={(e) => update((d) => void (d.variables[i].description = e.target.value))}
                    />
                  )}
                </Field>
                <Field label="Example">
                  {(p) => (
                    <Input
                      {...p}
                      value={v.example}
                      onChange={(e) => update((d) => void (d.variables[i].example = e.target.value))}
                    />
                  )}
                </Field>
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`Remove ${v.name || "detail"}`}
                  className="mb-1"
                  onClick={() => update((d) => void d.variables.splice(i, 1))}
                >
                  <Trash2 className="size-4" aria-hidden />
                </Button>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel
        title="Call atmosphere"
        description="Soft sounds under the agent's voice so the line never sounds dead or robotic."
      >
        <div className="flex max-w-xl flex-col gap-4">
          <Switch
            label="Office background"
            description="A quiet room tone for the whole call, like a real office line."
            checked={draft.atmosphere.room_tone}
            onChange={(on) => update((d) => void (d.atmosphere.room_tone = on))}
          />
          <Switch
            label="Typing while thinking"
            description="Soft keyboard sounds in the moment between the caller finishing and the agent replying."
            checked={draft.atmosphere.typing_while_thinking}
            onChange={(on) => update((d) => void (d.atmosphere.typing_while_thinking = on))}
          />
          <Field label={`Volume · ${Math.round(draft.atmosphere.volume * 100)}%`}>
            {(p) => (
              <input
                {...p}
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={draft.atmosphere.volume}
                onChange={(e) => update((d) => void (d.atmosphere.volume = Number(e.target.value)))}
                className="h-10 accent-[var(--ink)]"
              />
            )}
          </Field>
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
                    (d) => void (d.limits.max_duration_seconds = Math.max(1, Number(e.target.value)) * 60),
                  )
                }
              />
            )}
          </Field>
          <Field label="Hang up after silence (seconds)" hint="The agent checks once, then ends the call.">
            {(p) => (
              <Input
                {...p}
                type="number"
                min={5}
                max={120}
                value={draft.limits.silence_timeout_seconds}
                onChange={(e) =>
                  update((d) => void (d.limits.silence_timeout_seconds = Number(e.target.value)))
                }
              />
            )}
          </Field>
        </div>
      </Panel>
    </fieldset>
  );
}
