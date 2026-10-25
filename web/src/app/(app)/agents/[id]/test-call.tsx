"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Mic, PhoneOff } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Badge, Button, ErrorNotice, Field, Panel, Select } from "@/components/ui";
import type { AgentDetail } from "@/lib/api";
import { CallDetailsFields } from "@/components/call-details-fields";
import { callDetails, rememberValues, rememberedValues } from "@/lib/variables";
import { startBrowserCall, type LiveEvent } from "@/lib/browser-call";
import { LANGUAGE_NAMES } from "@/lib/format";

type Line = {
  id: number;
  who: "caller" | "agent" | "note";
  text: string;
  language: string | null;
  replySeconds?: number;
};
type Conversation = "listening" | "user_speaking" | "thinking" | "agent_speaking";

/** Wall-clock seconds, for timing how fast the agent replies. */
function clockSeconds(): number {
  return performance.now() / 1000;
}

const CONVERSATION_LABEL: Record<Conversation, string> = {
  listening: "Listening",
  user_speaking: "You're speaking",
  thinking: "Thinking…",
  agent_speaking: "Agent speaking",
};

export function TestCall({ agent }: { agent: AgentDetail }) {
  const queryClient = useQueryClient();
  const [versionId, setVersionId] = useState<string>(""); // "" = current edits
  const details = callDetails(agent.config);
  const [values, setValues] = useState<Record<string, string>>(() => rememberedValues(agent.id));
  const [state, setState] = useState<"idle" | "connecting" | "live" | "ended">("idle");
  const [error, setError] = useState<string | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [callId, setCallId] = useState<string | null>(null);
  const [level, setLevel] = useState(0);
  const [conversation, setConversation] = useState<Conversation>("listening");
  const [partial, setPartial] = useState("");
  const thinkingSince = useRef<number | null>(null);
  const lastReply = useRef<number | undefined>(undefined);
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
  }, [lines, partial]);

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
      case "state":
        setConversation(event.state);
        if (event.state === "thinking") thinkingSince.current = clockSeconds() * 1000;
        if (event.state === "agent_speaking" && thinkingSince.current !== null) {
          lastReply.current = (clockSeconds() * 1000 - thinkingSince.current) / 1000;
          thinkingSince.current = null;
        }
        break;
      case "partial":
        // Words appear as you speak; the full turn replaces them when you finish.
        setPartial(event.content);
        break;
      case "user_turn":
        setPartial("");
        push({ who: "caller", text: event.content, language: event.language });
        break;
      case "agent_turn":
        push({
          who: "agent",
          text: event.content,
          language: event.language,
          replySeconds: lastReply.current,
        });
        lastReply.current = undefined;
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
    setPartial("");
    setConversation("listening");
    setState("connecting");
    rememberValues(agent.id, values);
    try {
      session.current = await startBrowserCall(
        agent.id,
        versionId || null,
        Object.fromEntries(details.map((n) => [n, values[n] ?? ""])),
        {
          onEvent,
          onLevel: setLevel,
          onAgentSpeaking: () => {},
          onClose: (reason) => {
            if (reason) setError(reason);
            setState("ended");
            setPartial("");
            setLevel(0);
            queryClient.invalidateQueries({ queryKey: ["calls"] });
          },
        },
      );
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
          {details.length === 0 && (
            <p className="text-[13px] text-muted">
              This agent doesn&apos;t use any {"{{customer details}}"}.
            </p>
          )}
          <CallDetailsFields config={agent.config} values={values} onChange={setValues} disabled={live} />
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
                <span
                  className={`text-[13px] ${conversation === "thinking" ? "text-ink" : "text-muted"}`}
                  aria-live="polite"
                >
                  {CONVERSATION_LABEL[conversation]}
                </span>
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
            {lines.length === 0 && !partial ? (
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
                      {l.replySeconds !== undefined && ` · replied in ${l.replySeconds.toFixed(1)} s`}
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
            {partial && (
              <div className="max-w-[85%] self-end opacity-60">
                <p className="mb-0.5 text-right text-2xs text-muted">You · speaking</p>
                <p className="rounded-2xl rounded-tr-md border border-dashed border-line-strong px-3.5 py-2">
                  {partial}
                </p>
              </div>
            )}
            <div ref={bottom} />
          </div>
        </div>
      </Panel>
    </div>
  );
}
