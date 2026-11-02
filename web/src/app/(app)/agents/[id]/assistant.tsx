"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUp, Check, Undo2 } from "lucide-react";
import { Buddy } from "@/components/buddy";
import { ToolKeys } from "./tool-keys";
import { useEffect, useRef, useState } from "react";
import { Button, ErrorNotice, Skeleton } from "@/components/ui";
import { api, type AgentDetail, type AssistantMessage, type AssistantTurn } from "@/lib/api";
import { cn } from "@/lib/cn";

const SUGGESTIONS = [
  "Make the greeting warmer and shorter",
  "Add Malayalam and Marathi",
  "If the customer says they already paid, ask for the payment date",
  "Use a male voice named Ravi",
];

/**
 * Chat with the AI builder. Changes it makes go straight into the draft (never live until you
 * publish); the latest one can be undone.
 */
export function Assistant({
  agent,
  busy,
  onChanged,
}: {
  agent: AgentDetail;
  /** The editor has unsaved typing; wait for it so the assistant edits the latest draft. */
  busy: boolean;
  onChanged: (agent: AgentDetail) => void;
}) {
  const queryClient = useQueryClient();
  const key = ["agent-assistant", agent.id];
  const [text, setText] = useState("");
  const list = useRef<HTMLOListElement>(null);
  const history = useQuery({
    queryKey: key,
    queryFn: () => api<AssistantMessage[]>(`/agents/${agent.id}/assistant`),
  });

  const send = useMutation({
    mutationFn: (message: string) =>
      api<AssistantTurn>(`/agents/${agent.id}/assistant`, { method: "POST", json: { message } }),
    // Give the message back so it isn't lost.
    onError: (_, message) => setText((current) => current || message),
    onSuccess: (turn) => {
      // A new change replaces the old one as the undoable change.
      queryClient.setQueryData<AssistantMessage[]>(key, (old = []) => [
        ...old.map((m) => (turn.messages[1].can_undo ? { ...m, can_undo: false } : m)),
        ...turn.messages,
      ]);
      if (turn.messages[1].changes.length) onChanged(turn.agent);
      // YMA may have created or changed tools: their key fields show from the tool list.
      if (turn.messages[1].changes.some((c) => c.startsWith("Tool "))) {
        queryClient.invalidateQueries({ queryKey: ["tools"] });
      }
    },
  });
  const undo = useMutation({
    mutationFn: (messageId: string) =>
      api<AgentDetail>(`/agents/${agent.id}/assistant/${messageId}/undo`, { method: "POST" }),
    onSuccess: (updated, messageId) => {
      queryClient.setQueryData<AssistantMessage[]>(key, (old = []) =>
        old.map((m) => (m.id === messageId ? { ...m, can_undo: false, undone: true } : m)),
      );
      onChanged(updated);
    },
  });

  const count = history.data?.length ?? 0;
  useEffect(() => {
    const el = list.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [count, send.isPending]);

  const submit = (message: string) => {
    const trimmed = message.trim();
    if (!trimmed || send.isPending || busy) return;
    setText("");
    send.mutate(trimmed);
  };

  return (
    <section
      aria-label="YMA, the Yellow Metal Assistant"
      className="flex h-[26rem] flex-col rounded-xl border border-line xl:h-auto xl:max-h-[calc(100dvh-7rem)] xl:min-h-[30rem]"
    >
      <header className="flex items-start gap-3 border-b border-line px-5 py-4">
        <Buddy yma size={34} active={busy} />
        <div>
          <h2 className="text-[15px] font-semibold">
            YMA <span className="font-normal text-muted">· Yellow Metal Assistant</span>
          </h2>
          <p className="text-[13px] text-muted">
            Ask for changes in plain words. They go into your draft; nothing goes live until you publish.
          </p>
        </div>
      </header>

      <ol
        ref={list}
        className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 py-4"
        aria-live="polite"
      >
        {history.isPending ? (
          <li>
            <Skeleton className="h-16 rounded-lg" />
          </li>
        ) : history.isError ? (
          <li>
            <ErrorNotice error={history.error} onRetry={() => history.refetch()} />
          </li>
        ) : count === 0 && !send.isPending ? (
          <li className="flex flex-col gap-3">
            <p className="text-[13px] text-muted">Try one of these, or describe what you want:</p>
            <div className="flex flex-col items-start gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  disabled={busy}
                  onClick={() => submit(s)}
                  className="rounded-lg bg-surface px-3 py-2 text-left text-[13px] hover:bg-surface-hover disabled:opacity-50"
                >
                  {s}
                </button>
              ))}
            </div>
          </li>
        ) : (
          history.data.map((m) => (
            <li key={m.id} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
              {m.role === "user" ? (
                <UserMessage text={m.content} />
              ) : (
                <div className="flex max-w-[92%] gap-2.5">
                  <Buddy yma size={22} className="mt-0.5" />
                  <div className="flex min-w-0 flex-col gap-2">
                    <p className="text-[13px] leading-5 whitespace-pre-wrap">{m.content}</p>
                    {m.changes.length > 0 && (
                      <div className="rounded-lg border border-line px-3 py-2.5">
                        <p className="mb-1.5 text-2xs font-medium text-muted">
                          {m.undone ? "Undone" : "Changed in your draft"}
                        </p>
                        <ul className={cn("flex flex-col gap-1", m.undone && "text-faint line-through")}>
                          {m.changes.map((c) => (
                            <li key={c} className="flex gap-1.5 text-[13px]">
                              <Check
                                className={cn(
                                  "mt-0.5 size-3.5 shrink-0",
                                  m.undone ? "text-faint" : "text-success",
                                )}
                                aria-hidden
                              />
                              <span>{c}</span>
                            </li>
                          ))}
                        </ul>
                        {m.can_undo && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="-ml-2 mt-1.5"
                            loading={undo.isPending}
                            disabled={busy}
                            onClick={() => undo.mutate(m.id)}
                          >
                            <Undo2 className="size-3.5" aria-hidden /> Undo
                          </Button>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </li>
          ))
        )}
        <ToolKeys toolNames={[...(agent.config.tools ?? []), ...(agent.config.analysis?.send_to ?? [])]} />
        {send.isPending && (
          <>
            <li className="flex justify-end">
              <p className="max-w-[85%] rounded-2xl rounded-br-md bg-ink px-3.5 py-2 text-[13px] whitespace-pre-wrap text-on-ink opacity-70">
                {send.variables}
              </p>
            </li>
            <li className="flex items-center gap-2 text-[13px] text-muted">
              <span className="flex gap-1" aria-hidden>
                {[0, 150, 300].map((delay) => (
                  <span
                    key={delay}
                    className="size-1.5 animate-pulse rounded-full bg-faint"
                    style={{ animationDelay: `${delay}ms` }}
                  />
                ))}
              </span>
              Working on it…
            </li>
          </>
        )}
      </ol>

      <form
        className="border-t border-line p-3"
        onSubmit={(e) => {
          e.preventDefault();
          submit(text);
        }}
      >
        {(send.isError || undo.isError) && (
          <div className="mb-2">
            <ErrorNotice error={send.error ?? undo.error} />
          </div>
        )}
        <div className="flex items-end gap-2 rounded-xl bg-surface p-1.5 pl-3.5 focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-gold">
          <label htmlFor="assistant-input" className="sr-only">
            Message the assistant
          </label>
          <textarea
            id="assistant-input"
            rows={1}
            value={text}
            maxLength={4000}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                submit(text);
              }
            }}
            placeholder="e.g. Ask for the payment mode too"
            className="field-sizing-content max-h-40 min-h-8 flex-1 resize-none bg-transparent py-1.5 text-[13px] leading-5 outline-none placeholder:text-faint"
            // The box around it shows the focus ring instead.
            style={{ outline: "none" }}
          />
          <Button
            type="submit"
            size="sm"
            className="size-8 px-0"
            aria-label="Send"
            disabled={!text.trim() || busy}
            loading={send.isPending}
          >
            {!send.isPending && <ArrowUp className="size-4" aria-hidden />}
          </Button>
        </div>
        {busy && <p className="mt-1.5 px-1 text-2xs text-muted">Saving your edits first…</p>}
      </form>
    </section>
  );
}

/** A long message (like a talked-through interview) starts folded so the chat stays readable. */
function UserMessage({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 400;
  return (
    <div className="flex max-w-[85%] flex-col items-end gap-1">
      <p
        className={cn(
          "rounded-2xl rounded-br-md bg-ink px-3.5 py-2 text-[13px] whitespace-pre-wrap text-on-ink",
          long && !open && "line-clamp-4",
        )}
      >
        {text}
      </p>
      {long && (
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="text-2xs text-muted underline underline-offset-4"
        >
          {open ? "Show less" : "Show all"}
        </button>
      )}
    </div>
  );
}
