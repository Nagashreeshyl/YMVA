"use client";

import { useMutation } from "@tanstack/react-query";
import { Bot, Mic, PhoneOff } from "lucide-react";
import { Buddy } from "@/components/buddy";
import { useEffect, useRef, useState } from "react";
import { Badge, Button, ErrorNotice, Field, Input } from "@/components/ui";
import { api, type AgentDetail } from "@/lib/api";
import { startTalk, type LiveEvent } from "@/lib/browser-call";

type Line = { id: number; who: "you" | "designer"; text: string };

/** Describe a new agent by talking it through; the agent is built from the conversation. */
export function TalkToBuild({
  onBuilt,
  onCancel,
}: {
  onBuilt: (agent: AgentDetail) => void;
  onCancel: () => void;
}) {
  const [state, setState] = useState<"idle" | "connecting" | "live" | "ended">("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const [partial, setPartial] = useState("");
  const [callId, setCallId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [seconds, setSeconds] = useState(0);
  const [speaking, setSpeaking] = useState(false);
  const session = useRef<{ stop: () => void } | null>(null);
  const counter = useRef(0);
  const bottom = useRef<HTMLDivElement>(null);
  const build = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<AgentDetail>("/agents/generate", { method: "POST", json: body }),
    onSuccess: onBuilt,
  });

  useEffect(() => () => session.current?.stop(), []);
  useEffect(() => {
    if (state !== "live") return;
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [state]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest" });
  }, [lines, partial]);

  function onEvent(event: LiveEvent) {
    const push = (who: Line["who"], text: string) =>
      setLines((l) => [...l, { id: ++counter.current, who, text }]);
    if (event.type === "call") {
      setCallId(event.call_id);
      setState("live");
    } else if (event.type === "error") setError(event.content);
    else if (event.type === "partial") setPartial(event.content);
    else if (event.type === "user_turn") {
      setPartial("");
      push("you", event.content);
    } else if (event.type === "agent_turn") push("designer", event.content);
  }

  async function start() {
    setError(null);
    setLines([]);
    setPartial("");
    setSeconds(0);
    setCallId(null);
    setState("connecting");
    try {
      session.current = await startTalk("/api/calls/web/builder", {
        onEvent,
        onLevel: () => {},
        onAgentSpeaking: setSpeaking,
        onClose: (reason) => {
          if (reason) setError(reason);
          setPartial("");
          setSpeaking(false);
          setState("ended");
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
  const said = lines.some((l) => l.who === "you");
  const clock = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start gap-3">
        <Buddy yma size={44} active={speaking} />
        <p className="text-[13px] text-muted">
          Talk to YMA, the Yellow Metal Assistant, like you&apos;d brief a new colleague: what the agent is
          for, how it should speak, what to say first and what to ask next, the dos and don&apos;ts. It asks
          about anything you miss. When you&apos;re done, end the call and build the agent. You can still edit
          everything afterwards.
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {live ? (
          <Button variant="primary" className="bg-danger text-paper" onClick={() => session.current?.stop()}>
            <PhoneOff className="size-4" aria-hidden /> End call
          </Button>
        ) : (
          <Button onClick={start} variant={said ? "ghost" : "primary"} disabled={build.isPending}>
            <Mic className="size-4" aria-hidden /> {state === "ended" ? "Talk again" : "Start talking"}
          </Button>
        )}
        {state === "connecting" && <span className="text-[13px] text-muted">Connecting…</span>}
        {state === "live" && (
          <Badge tone="gold">
            <span className="size-1.5 animate-pulse rounded-full bg-ink" aria-hidden /> Live · {clock}
          </Badge>
        )}
        {!live && (
          <Button type="button" variant="ghost" onClick={onCancel} disabled={build.isPending}>
            Cancel
          </Button>
        )}
      </div>
      {error && <ErrorNotice error={new Error(error)} />}
      {(lines.length > 0 || partial || live) && (
        <div
          className="flex max-h-[360px] min-h-32 flex-col gap-2 overflow-y-auto rounded-xl bg-rail p-4"
          aria-live="polite"
        >
          {lines.length === 0 && !partial && (
            <p className="m-auto text-[13px] text-muted">Connecting to YMA…</p>
          )}
          {lines.map((l) => (
            <div
              key={l.id}
              className={l.who === "designer" ? "max-w-[85%] self-start" : "max-w-[85%] self-end"}
            >
              <p className="mb-0.5 text-2xs text-muted">{l.who === "designer" ? "YMA" : "You"}</p>
              <p
                className={
                  l.who === "designer"
                    ? "rounded-2xl rounded-tl-md bg-paper px-3.5 py-2"
                    : "rounded-2xl rounded-tr-md bg-ink px-3.5 py-2 text-on-ink"
                }
              >
                {l.text}
              </p>
            </div>
          ))}
          {partial && (
            <p className="max-w-[85%] self-end rounded-2xl rounded-tr-md border border-dashed border-line-strong px-3.5 py-2 opacity-60">
              {partial}
            </p>
          )}
          <div ref={bottom} />
        </div>
      )}
      {state === "ended" && said && callId && (
        <form
          className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            build.mutate({
              call_id: callId,
              name: String(f.get("name") ?? "") || null,
            });
          }}
        >
          <Field label="Name (optional)" hint="Leave empty and the AI names it.">
            {(p) => <Input {...p} name="name" maxLength={120} placeholder="e.g. Hospital appointments" />}
          </Field>
          <Button type="submit" loading={build.isPending}>
            {!build.isPending && <Bot className="size-4" aria-hidden />}
            {build.isPending ? "Building your agent…" : "Build agent from this conversation"}
          </Button>
        </form>
      )}
      {build.error && <ErrorNotice error={build.error} />}
    </div>
  );
}
