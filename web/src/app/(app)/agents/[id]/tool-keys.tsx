"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Lock } from "lucide-react";
import { useState } from "react";
import { Button, ErrorNotice, Field, Input, Textarea } from "@/components/ui";
import { api, type ToolDef } from "@/lib/api";

/** What a tool's secure field asks for, by tool type. */
const ASK: Record<string, string> = {
  google_sheets: "Paste the service account key (the whole JSON file). Share the sheet with its email.",
  http: "The API's key or token, sent in a header.",
  database: "The database connection, e.g. postgresql://user:password@host:5432/db",
};

function KeyForm({ tool }: { tool: ToolDef }) {
  const queryClient = useQueryClient();
  const [header, setHeader] = useState("Authorization");
  const [value, setValue] = useState("");
  const save = useMutation({
    mutationFn: () => {
      let secrets: Record<string, unknown>;
      if (tool.type === "google_sheets") {
        try {
          secrets = { service_account: JSON.parse(value) };
        } catch {
          throw new Error("That isn't the JSON key file's content. Paste the whole file.");
        }
      } else if (tool.type === "database") {
        secrets = { connection: value.trim() };
      } else {
        secrets = { headers: { [header.trim() || "Authorization"]: value.trim() } };
      }
      return api<ToolDef>(`/tools/${tool.id}/secrets`, { method: "PUT", json: { secrets } });
    },
    onSuccess: () => {
      setValue("");
      queryClient.invalidateQueries({ queryKey: ["tools"] });
    },
  });
  return (
    <form
      className="flex flex-col gap-2.5 rounded-xl border border-gold/40 bg-rail p-3.5"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <p className="flex items-center gap-1.5 text-[13px] font-medium">
        <KeyRound className="size-3.5 text-chart-1" aria-hidden /> Key for {tool.name}
      </p>
      <p className="text-2xs text-muted">{ASK[tool.type] ?? "The key this tool needs."}</p>
      {tool.type === "http" && (
        <Field label="Header">
          {(p) => <Input {...p} value={header} onChange={(e) => setHeader(e.target.value)} />}
        </Field>
      )}
      {tool.type === "google_sheets" ? (
        <Textarea
          aria-label={`Key for ${tool.name}`}
          rows={3}
          className="min-h-0 font-mono text-2xs"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          autoComplete="off"
          spellCheck={false}
          placeholder='{"type": "service_account", ...}'
        />
      ) : (
        <Input
          aria-label={`Key for ${tool.name}`}
          type="password"
          autoComplete="off"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={tool.type === "http" ? "Bearer …" : "postgresql://…"}
        />
      )}
      <p className="flex items-center gap-1.5 text-2xs text-muted">
        <Lock className="size-3" aria-hidden /> Saved encrypted on the tool. It isn&apos;t kept in this chat
        or sent to the AI.
      </p>
      {save.isError && <ErrorNotice error={save.error} />}
      <Button type="submit" size="sm" className="w-fit" loading={save.isPending} disabled={!value.trim()}>
        Save key
      </Button>
    </form>
  );
}

/** Secure fields for this agent's tools that still need a key, shown inside YMA's chat. */
export function ToolKeys({ toolNames }: { toolNames: string[] }) {
  const tools = useQuery({
    queryKey: ["tools"],
    queryFn: () => api<ToolDef[]>("/tools"),
    enabled: toolNames.length > 0,
  });
  const waiting = (tools.data ?? []).filter((t) => toolNames.includes(t.name) && t.missing.includes("key"));
  if (!waiting.length) return null;
  return (
    <li className="flex flex-col gap-2">
      {waiting.map((t) => (
        <KeyForm key={t.id} tool={t} />
      ))}
    </li>
  );
}
