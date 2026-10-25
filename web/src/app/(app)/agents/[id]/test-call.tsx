"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Mic, PhoneOff } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Badge, Button, ErrorNotice, Field, Input, Panel, Select } from "@/components/ui";
import type { AgentDetail } from "@/lib/api";
import { startBrowserCall, type LiveEvent } from "@/lib/browser-call";
import { LANGUAGE_NAMES } from "@/lib/format";

type Line = { id: number; who: "caller" | "agent" | "note"; text: string; language: string | null };

export function TestCall({ agent }: { agent: AgentDetail }) {
  const queryClient = useQueryClient();
  const [versionId, setVersionId] = useState<string>(""); // "" = current edits
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(agent.config.variables.map((v) => [v.name, v.example])),
  );
  const [state, setState] = useState<"idle" | "connecting" | "live" | "ended">("idle");
  const [error, setError] = useState<string | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [callId, setCallId] = useState<string | null>(null);
  const [level, setLevel] = useState(0);
  const [agentSpeaking, setAgentSpeaking] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const session = useRef<{ stop: () => void } | null>(null);
  const counter = useRef(0);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => () => session.current?.stop(), []);
  useEffect(() => {
    if (state !== "live") return;
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [state]);
  useEffect(() => {
    // Braces matter: newer browsers return a Promise from scrollIntoView, and an effect
    // must return nothing or a cleanup function.
    bottom.current?.scrollIntoView({ block: "nearest" });
  }, [lines]);

  const push = (line: Omit<Line, "id">) => setLines((l) => [...l, { ...line, id: ++counter.current }]);

  function onEvent(event: LiveEvent) {
    switch (event.type) {
      case "call":
        setCallId(event.call_id);
        setState("live");
        break;
      case "error":
        setError(event.content);
        break;
      case "user_turn":
        push({ who: "caller", text: event.content, language: event.language });
        break;
      case "agent_turn":
        push({ who: "agent", text: event.content, language: event.language });
        break;
      case "language_switch":
        push({ who: "note", text: `Language: ${event.content}`, language: event.language });
        break;
      case "tool_call":
        push({ who: "note", text: `Agent used ${event.content}`, language: null });
        break;
    }
  }

  async function start() {
    setError(null);
    setLines([]);
    setSeconds(0);
    setCallId(null);
    setState("connecting");
    try {
      session.current = await startBrowserCall(agent.id, versionId || null, values, {
        onEvent,
        onLevel: setLevel,
        onAgentSpeaking: setAgentSpeaking,
        onClose: (reason) => {
          if (reason) setError(reason);
          setState("ended");
          setAgentSpeaking(false);
          setLevel(0);
          queryClient.invalidateQueries({ queryKey: ["calls"] });
        },
      });
    } catch (err) {
      setState("idle");
      setError(
        err instanceof DOMException && err.name === "NotAllowedError"
          ? "Microphone access was blocked. Allow it in your browser's site settings and try again."
          : "Couldn't start the microphone. Check that one is connected.",
      );
    }
  }

  const live = state === "connecting" || state === "live";
  const clock = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;

  return (
    <div className="grid items-start gap-6 lg:grid-cols-[320px_1fr]">
      <Panel title="Test values" description="Used as this call's customer details.">
        <div className="flex flex-col gap-3">
          <Field label="Test with">
            {(p) => (
              <Select {...p} value={versionId} disabled={live} onChange={(e) => setVersionId(e.target.value)}>
                <option value="">Current edits{agent.has_draft_changes ? " (not yet a version)" : ""}</option>
                {agent.versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    Version {v.version}
                    {v.id === agent.published_version_id ? " · live" : ""}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          {agent.config.variables.length === 0 && (
            <p className="text-[13px] text-muted">This agent has no customer details.</p>
          )}
          {agent.config.variables.map((v) => (
            <Field key={v.name} label={v.name} hint={v.description}>
              {(p) => (
                <Input
                  {...p}
                  value={values[v.name] ?? ""}
                  disabled={live}
                  onChange={(e) => setValues((x) => ({ ...x, [v.name]: e.target.value }))}
                />
              )}
            </Field>
          ))}
        </div>
      </Panel>

      <Panel
        title="Talk to the agent"
        description="Uses your microphone and speakers. Headphones avoid echo."
      >
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-3">
            {live ? (
              <Button
                variant="primary"
                className="bg-danger text-paper"
                onClick={() => session.current?.stop()}
              >
                <PhoneOff className="size-4" aria-hidden /> End call
              </Button>
            ) : (
              <Button onClick={start}>
                <Mic className="size-4" aria-hidden /> {state === "ended" ? "Call again" : "Start test call"}
              </Button>
            )}
            {state === "connecting" && <span className="text-[13px] text-muted">Connecting…</span>}
            {state === "live" && (
              <>
                <Badge tone="gold">
                  <span className="size-1.5 animate-pulse rounded-full bg-ink" aria-hidden /> Live · {clock}
                </Badge>
                <span
                  className="flex items-center gap-2 text-2xs text-muted"
                  aria-label="Your microphone level"
                >
                  You
                  <span className="h-1.5 w-20 overflow-hidden rounded-full bg-surface">
                    <span
                      className="block h-full rounded-full bg-chart-1 transition-[width] duration-100"
                      style={{ width: `${Math.min(100, level * 300)}%` }}
                    />
                  </span>
                </span>
                {agentSpeaking && <span className="text-2xs text-muted">Agent speaking…</span>}
              </>
            )}
            {state === "ended" && callId && (
              <Link
                href={`/calls/${callId}`}
                className="text-[13px] font-medium underline underline-offset-4"
              >
                Open call record
              </Link>
            )}
          </div>
          {error && <ErrorNotice error={new Error(error)} />}
          <div
            className="flex max-h-[420px] min-h-40 flex-col gap-2 overflow-y-auto rounded-xl bg-rail p-4"
            aria-live="polite"
          >
            {lines.length === 0 ? (
              <p className="m-auto text-[13px] text-muted">
                {live ? "Listening… say hello." : "The conversation will appear here as you talk."}
              </p>
            ) : (
              lines.map((l) =>
                l.who === "note" ? (
                  <p key={l.id} className="self-center text-2xs text-muted">
                    {l.text}
                  </p>
                ) : (
                  <div
                    key={l.id}
                    className={l.who === "agent" ? "max-w-[85%] self-start" : "max-w-[85%] self-end"}
                  >
                    <p className="mb-0.5 text-2xs text-muted">
                      {l.who === "agent" ? "Agent" : "You"}
                      {l.language && ` · ${LANGUAGE_NAMES[l.language] ?? l.language}`}
                    </p>
                    <p
                      className={
                        l.who === "agent"
                          ? "rounded-2xl rounded-tl-md bg-paper px-3.5 py-2"
                          : "rounded-2xl rounded-tr-md bg-ink px-3.5 py-2 text-on-ink"
                      }
                    >
                      {l.text}
                    </p>
                  </div>
                ),
              )
            )}
            <div ref={bottom} />
          </div>
        </div>
      </Panel>
    </div>
  );
}
