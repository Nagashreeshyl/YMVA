"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Database,
  FileSpreadsheet,
  Globe,
  Plus,
  Sparkles,
  Wrench,
  X,
} from "lucide-react";
import { useState } from "react";
import { useConfirm } from "@/components/dialog";
import {
  Badge,
  Button,
  EmptyState,
  ErrorNotice,
  Field,
  Input,
  PageHeader,
  Panel,
  Segmented,
  Select,
  Skeleton,
  Switch,
  Textarea,
} from "@/components/ui";
import {
  api,
  type AgentDetail,
  type AgentSummary,
  type DatabaseToolConfig,
  type HttpToolConfig,
  type SheetsToolConfig,
  type ToolDef,
  type ToolParam,
  type ToolType,
} from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { detectVariables } from "@/lib/variables";
import { useMe } from "@/lib/session";

type Draft = Omit<
  ToolDef,
  "id" | "has_secrets" | "secret_hint" | "used_by" | "updated_at"
>;

const NEW_SHEET: SheetsToolConfig = {
  spreadsheet: "",
  sheet: "Sheet1",
  action: "append_row",
  columns: [{ header: "", value: "" }],
  match_column: "",
  match_value: "",
};
const NEW_HTTP: HttpToolConfig = {
  method: "POST",
  url: "https://",
  headers: {},
  body: "",
};

const NEW_DB: DatabaseToolConfig = { query: "", mode: "read" };
const NEW_CONFIG: Record<ToolType, Draft["config"]> = {
  google_sheets: NEW_SHEET,
  http: NEW_HTTP,
  database: NEW_DB,
};

const TYPE_LABEL: Record<ToolType, string> = {
  google_sheets: "Google Sheets",
  http: "HTTP request",
  database: "Database",
};

function blankDraft(): Draft {
  return {
    name: "",
    description: "",
    type: "google_sheets",
    config: NEW_SHEET,
    params: [],
    is_active: true,
  };
}

/** Every {{name}} used in the tool's settings: what the test form asks for. */
function placeholders(config: Draft["config"]): string[] {
  return [
    ...new Set(
      [
        ...JSON.stringify(config).matchAll(
          /\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g,
        ),
      ].map((m) => m[1]),
    ),
  ];
}

function ToolForm({
  tool,
  onDone,
}: {
  tool: ToolDef | null;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const [draft, setDraft] = useState<Draft>(() =>
    tool
      ? {
          name: tool.name,
          description: tool.description,
          type: tool.type,
          config: tool.config,
          params: tool.params,
          is_active: tool.is_active,
        }
      : blankDraft(),
  );
  const [brief, setBrief] = useState("");
  // Which agent the tool is for: its customer details become placeholders, and it's switched on there.
  const [agentId, setAgentId] = useState("");
  const agents = useQuery({
    queryKey: ["agents", "active"],
    queryFn: () => api<AgentSummary[]>("/agents"),
    enabled: !tool,
  });
  const agent = useQuery({
    queryKey: ["agent", agentId],
    queryFn: () => api<AgentDetail>(`/agents/${agentId}`),
    enabled: Boolean(agentId),
  });
  const agentDetails = agent.data ? detectVariables(agent.data.config) : [];
  // Only sent when typed: stored secrets are never shown again.
  const [keyJson, setKeyJson] = useState("");
  const [connection, setConnection] = useState("");
  const [secretHeaders, setSecretHeaders] = useState<
    { name: string; value: string }[]
  >([]);
  const [saved, setSaved] = useState<ToolDef | null>(tool);
  const [testValues, setTestValues] = useState<Record<string, string>>({});
  const set = (change: Partial<Draft>) =>
    setDraft((d) => ({ ...d, ...change }));
  const sheet = draft.config as SheetsToolConfig;
  const http = draft.config as HttpToolConfig;
  const db = draft.config as DatabaseToolConfig;
  const setConfig = (
    change: Partial<SheetsToolConfig & HttpToolConfig & DatabaseToolConfig>,
  ) =>
    setDraft((d) => ({
      ...d,
      config: { ...d.config, ...change } as Draft["config"],
    }));

  const generate = useMutation({
    mutationFn: () =>
      api<Draft>("/tools/generate", {
        method: "POST",
        json: { description: brief, agent_id: agentId || null },
      }),
    onSuccess: (generated) => setDraft({ ...generated, is_active: true }),
  });

  const secrets = (): Record<string, unknown> | undefined => {
    if (draft.type === "google_sheets" && keyJson.trim()) {
      return { service_account: JSON.parse(keyJson) };
    }
    if (draft.type === "database" && connection.trim()) {
      return { connection: connection.trim() };
    }
    if (draft.type === "http" && secretHeaders.some((h) => h.name.trim())) {
      return {
        headers: Object.fromEntries(
          secretHeaders
            .filter((h) => h.name.trim())
            .map((h) => [h.name, h.value]),
        ),
      };
    }
    return undefined;
  };
  const save = useMutation({
    mutationFn: () => {
      let body: Record<string, unknown>;
      try {
        body = {
          ...draft,
          secrets: secrets(),
          agent_id: saved ? null : agentId || null,
        };
      } catch {
        throw new Error(
          "The service account key isn't valid JSON. Paste the whole file.",
        );
      }
      return saved
        ? api<ToolDef>(`/tools/${saved.id}`, { method: "PUT", json: body })
        : api<ToolDef>("/tools", { method: "POST", json: body });
    },
    onSuccess: (result) => {
      setSaved(result);
      setKeyJson("");
      setConnection("");
      setSecretHeaders([]);
      queryClient.invalidateQueries({ queryKey: ["tools"] });
    },
  });
  const test = useMutation({
    mutationFn: () =>
      api<Record<string, unknown>>(`/tools/${saved?.id}/test`, {
        method: "POST",
        json: { values: testValues },
      }),
  });
  const remove = useMutation({
    mutationFn: () => api(`/tools/${saved?.id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tools"] });
      onDone();
    },
  });

  const editParams = (params: ToolParam[]) => set({ params });
  const asked = placeholders(draft.config);

  return (
    <div className="flex flex-col gap-6">
      {!saved && (
        <Panel
          title="Describe the tool"
          description="Say what it should do in plain words and the AI fills in the form below. You can also fill it in yourself."
        >
          <div className="flex flex-col gap-3">
            <Field
              label="Which agent is this tool for?"
              hint="The tool can use that agent's customer details, and it's switched on for the agent when you create it."
            >
              {(p) => (
                <Select
                  {...p}
                  value={agentId}
                  onChange={(e) => setAgentId(e.target.value)}
                >
                  <option value="">Choose an agent…</option>
                  {agents.data?.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            {agentDetails.length > 0 && (
              <p className="flex flex-wrap items-center gap-1.5 text-2xs text-muted">
                Available from {agent.data?.name}:
                {[
                  ...agentDetails,
                  "call_date",
                  "call_time",
                  "customer_phone",
                ].map((v) => (
                  <code
                    key={v}
                    className="rounded bg-surface px-1.5 py-0.5 font-mono text-ink"
                  >{`{{${v}}}`}</code>
                ))}
              </p>
            )}
            <Textarea
              aria-label="What the tool should do"
              rows={3}
              value={brief}
              onChange={(e) => setBrief(e.target.value)}
              placeholder="e.g. When the customer promises to pay, add a row to our collections sheet with the loan ID, customer name, promised date and the call time."
            />
            <Button
              variant="secondary"
              className="w-fit"
              disabled={brief.trim().length < 10}
              loading={generate.isPending}
              onClick={() => generate.mutate()}
            >
              {!generate.isPending && (
                <Sparkles className="size-4" aria-hidden />
              )}{" "}
              Fill in with AI
            </Button>
            {generate.isError && <ErrorNotice error={generate.error} />}
          </div>
        </Panel>
      )}

      <Panel title="What it does">
        <div className="flex flex-col gap-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              label="Name"
              hint="Lowercase with underscores, e.g. save_payment_promise"
            >
              {(p) => (
                <Input
                  {...p}
                  value={draft.name}
                  maxLength={64}
                  disabled={Boolean(saved && saved.used_by.length)}
                  onChange={(e) =>
                    set({
                      name: e.target.value
                        .toLowerCase()
                        .replace(/[^a-z0-9_]/g, "_"),
                    })
                  }
                />
              )}
            </Field>
            <div className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium">Type</span>
              <Segmented
                label="Tool type"
                value={draft.type}
                onChange={(type) =>
                  set({
                    type,
                    config: NEW_CONFIG[type],
                  })
                }
                options={[
                  { value: "google_sheets", label: "Google Sheets" },
                  { value: "http", label: "HTTP request" },
                  { value: "database", label: "Database" },
                ]}
              />
            </div>
          </div>
          <Field
            label="When the agent should use it"
            hint="The agent reads this to decide when to use the tool, so be specific."
          >
            {(p) => (
              <Textarea
                {...p}
                rows={2}
                className="min-h-0"
                maxLength={1000}
                value={draft.description}
                onChange={(e) => set({ description: e.target.value })}
              />
            )}
          </Field>
          <Switch
            label="Active"
            description="Switched off, agents stop using it until you switch it back on."
            checked={draft.is_active}
            onChange={(is_active) => set({ is_active })}
          />
        </div>
      </Panel>

      <Panel
        title="What the agent fills in"
        description="Details the agent gets from the customer during the call. Use them below as {{name}}, along with the call's customer details (e.g. {{loan_id}}) and {{call_date}}, {{call_time}}, {{customer_phone}}."
      >
        <div className="flex flex-col gap-2">
          {draft.params.map((param, i) => (
            <div
              key={i}
              className="grid gap-2 sm:grid-cols-[200px_1fr_auto_auto] sm:items-center"
            >
              <Input
                aria-label={`Detail ${i + 1}: name`}
                placeholder="promised_date"
                value={param.name}
                onChange={(e) =>
                  editParams(
                    draft.params.map((x, j) =>
                      j === i
                        ? {
                            ...x,
                            name: e.target.value
                              .toLowerCase()
                              .replace(/[^a-z0-9_]/g, "_"),
                          }
                        : x,
                    ),
                  )
                }
              />
              <Input
                aria-label={`Detail ${i + 1}: description`}
                placeholder="Date the customer will pay, as YYYY-MM-DD"
                value={param.description}
                onChange={(e) =>
                  editParams(
                    draft.params.map((x, j) =>
                      j === i ? { ...x, description: e.target.value } : x,
                    ),
                  )
                }
              />
              <label className="flex items-center gap-1.5 text-[13px] text-muted">
                <input
                  type="checkbox"
                  checked={param.required}
                  onChange={(e) =>
                    editParams(
                      draft.params.map((x, j) =>
                        j === i ? { ...x, required: e.target.checked } : x,
                      ),
                    )
                  }
                />
                Required
              </label>
              <Button
                variant="ghost"
                size="sm"
                aria-label={`Remove ${param.name || `detail ${i + 1}`}`}
                onClick={() =>
                  editParams(draft.params.filter((_, j) => j !== i))
                }
              >
                <X className="size-4" aria-hidden />
              </Button>
            </div>
          ))}
          <Button
            variant="secondary"
            size="sm"
            className="w-fit"
            onClick={() =>
              editParams([
                ...draft.params,
                { name: "", description: "", required: true },
              ])
            }
          >
            <Plus className="size-3.5" aria-hidden /> Add detail
          </Button>
        </div>
      </Panel>

      {draft.type === "google_sheets" ? (
        <Panel title="Google Sheet">
          <div className="flex flex-col gap-4">
            <div className="grid gap-4 sm:grid-cols-[1fr_200px_200px]">
              <Field label="Sheet link">
                {(p) => (
                  <Input
                    {...p}
                    placeholder="https://docs.google.com/spreadsheets/d/…"
                    value={sheet.spreadsheet}
                    onChange={(e) => setConfig({ spreadsheet: e.target.value })}
                  />
                )}
              </Field>
              <Field label="Tab">
                {(p) => (
                  <Input
                    {...p}
                    value={sheet.sheet}
                    onChange={(e) => setConfig({ sheet: e.target.value })}
                  />
                )}
              </Field>
              <Field label="Action">
                {(p) => (
                  <Select
                    {...p}
                    value={sheet.action}
                    onChange={(e) =>
                      setConfig({
                        action: e.target.value as SheetsToolConfig["action"],
                      })
                    }
                  >
                    <option value="append_row">Add a row</option>
                    <option value="find_row">Look up a row</option>
                  </Select>
                )}
              </Field>
            </div>
            {sheet.action === "append_row" ? (
              <div className="flex flex-col gap-2">
                <p className="text-[13px] font-medium">Columns to fill</p>
                <p className="text-2xs text-muted">
                  Column titles must match the sheet&apos;s first row.
                </p>
                {sheet.columns.map((col, i) => (
                  <div
                    key={i}
                    className="grid gap-2 sm:grid-cols-[200px_1fr_auto] sm:items-center"
                  >
                    <Input
                      aria-label={`Column ${i + 1}: title`}
                      placeholder="Promised date"
                      value={col.header}
                      onChange={(e) =>
                        setConfig({
                          columns: sheet.columns.map((c, j) =>
                            j === i ? { ...c, header: e.target.value } : c,
                          ),
                        })
                      }
                    />
                    <Input
                      aria-label={`Column ${i + 1}: value`}
                      placeholder="{{promised_date}}"
                      value={col.value}
                      onChange={(e) =>
                        setConfig({
                          columns: sheet.columns.map((c, j) =>
                            j === i ? { ...c, value: e.target.value } : c,
                          ),
                        })
                      }
                    />
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={`Remove column ${col.header || i + 1}`}
                      onClick={() =>
                        setConfig({
                          columns: sheet.columns.filter((_, j) => j !== i),
                        })
                      }
                    >
                      <X className="size-4" aria-hidden />
                    </Button>
                  </div>
                ))}
                <Button
                  variant="secondary"
                  size="sm"
                  className="w-fit"
                  onClick={() =>
                    setConfig({
                      columns: [...sheet.columns, { header: "", value: "" }],
                    })
                  }
                >
                  <Plus className="size-3.5" aria-hidden /> Add column
                </Button>
              </div>
            ) : (
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Find the row where column" hint="e.g. Loan ID">
                  {(p) => (
                    <Input
                      {...p}
                      value={sheet.match_column}
                      onChange={(e) =>
                        setConfig({ match_column: e.target.value })
                      }
                    />
                  )}
                </Field>
                <Field label="equals" hint="e.g. {{loan_id}}">
                  {(p) => (
                    <Input
                      {...p}
                      value={sheet.match_value}
                      onChange={(e) =>
                        setConfig({ match_value: e.target.value })
                      }
                    />
                  )}
                </Field>
              </div>
            )}
            <Field
              label={
                saved?.has_secrets
                  ? "Service account key · saved"
                  : "Service account key"
              }
              hint={
                saved?.secret_hint
                  ? `Share the sheet with ${saved.secret_hint} as an editor. Paste a new key only to replace it.`
                  : "Paste the JSON key of a Google Cloud service account (with the Sheets API enabled), then share the sheet with its email as an editor."
              }
            >
              {(p) => (
                <Textarea
                  {...p}
                  rows={3}
                  className="min-h-0 font-mono text-[12px]"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder={
                    saved?.has_secrets
                      ? "Stored. Paste to replace."
                      : '{"type": "service_account", …}'
                  }
                  value={keyJson}
                  onChange={(e) => setKeyJson(e.target.value)}
                />
              )}
            </Field>
          </div>
        </Panel>
      ) : draft.type === "database" ? (
        <Panel title="Database (PostgreSQL)">
          <div className="flex flex-col gap-4">
            <Field
              label="SQL query"
              hint="Put {{name}} where a value goes, e.g. … where loan_id = {{loan_id}}. Values are passed safely as query parameters, never pasted into the SQL."
            >
              {(p) => (
                <Textarea
                  {...p}
                  rows={4}
                  className="min-h-0 font-mono text-[12px]"
                  spellCheck={false}
                  placeholder="select interest_due, due_date from loans where loan_id = {{loan_id}}"
                  value={db.query}
                  onChange={(e) => setConfig({ query: e.target.value })}
                />
              )}
            </Field>
            <Field
              label="Access"
              hint="Read-only tools can't change any data, even by mistake. Shows up to 20 rows."
            >
              {(p) => (
                <Select
                  {...p}
                  value={db.mode}
                  onChange={(e) =>
                    setConfig({
                      mode: e.target.value as DatabaseToolConfig["mode"],
                    })
                  }
                >
                  <option value="read">Read only (look things up)</option>
                  <option value="write">
                    Can change data (insert or update)
                  </option>
                </Select>
              )}
            </Field>
            <Field
              label={
                saved?.has_secrets
                  ? `Database connection · saved (${saved.secret_hint})`
                  : "Database connection"
              }
              hint="postgresql://user:password@host:5432/database. Stored encrypted and never shown again. Use a database user with only the access this tool needs."
            >
              {(p) => (
                <Input
                  {...p}
                  type="password"
                  autoComplete="off"
                  placeholder={
                    saved?.has_secrets
                      ? "Stored. Type to replace."
                      : "postgresql://…"
                  }
                  value={connection}
                  onChange={(e) => setConnection(e.target.value)}
                />
              )}
            </Field>
          </div>
        </Panel>
      ) : (
        <Panel title="HTTP request">
          <div className="flex flex-col gap-4">
            <div className="grid gap-4 sm:grid-cols-[120px_1fr]">
              <Field label="Method">
                {(p) => (
                  <Select
                    {...p}
                    value={http.method}
                    onChange={(e) =>
                      setConfig({
                        method: e.target.value as HttpToolConfig["method"],
                      })
                    }
                  >
                    {["GET", "POST", "PUT", "PATCH"].map((m) => (
                      <option key={m}>{m}</option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field
                label="URL"
                hint="May use {{placeholders}}, e.g. …/loans/{{loan_id}}"
              >
                {(p) => (
                  <Input
                    {...p}
                    value={http.url}
                    onChange={(e) => setConfig({ url: e.target.value })}
                  />
                )}
              </Field>
            </div>
            {http.method !== "GET" && (
              <Field
                label="Body (JSON)"
                hint="Leave empty to send the agent's details as a JSON object."
              >
                {(p) => (
                  <Textarea
                    {...p}
                    rows={3}
                    className="min-h-0 font-mono text-[12px]"
                    placeholder='{"loan_id": "{{loan_id}}", "promised_date": "{{promised_date}}"}'
                    value={http.body}
                    onChange={(e) => setConfig({ body: e.target.value })}
                  />
                )}
              </Field>
            )}
            <div className="flex flex-col gap-2">
              <p className="text-[13px] font-medium">
                Secret headers
                {saved?.secret_hint ? ` · saved: ${saved.secret_hint}` : ""}
              </p>
              <p className="text-2xs text-muted">
                For API keys, e.g. Authorization. Stored encrypted and never
                shown again.
              </p>
              {secretHeaders.map((h, i) => (
                <div
                  key={i}
                  className="grid gap-2 sm:grid-cols-[200px_1fr_auto]"
                >
                  <Input
                    aria-label={`Secret header ${i + 1}: name`}
                    placeholder="Authorization"
                    value={h.name}
                    onChange={(e) =>
                      setSecretHeaders(
                        secretHeaders.map((x, j) =>
                          j === i ? { ...x, name: e.target.value } : x,
                        ),
                      )
                    }
                  />
                  <Input
                    aria-label={`Secret header ${i + 1}: value`}
                    type="password"
                    autoComplete="off"
                    value={h.value}
                    onChange={(e) =>
                      setSecretHeaders(
                        secretHeaders.map((x, j) =>
                          j === i ? { ...x, value: e.target.value } : x,
                        ),
                      )
                    }
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={`Remove secret header ${i + 1}`}
                    onClick={() =>
                      setSecretHeaders(secretHeaders.filter((_, j) => j !== i))
                    }
                  >
                    <X className="size-4" aria-hidden />
                  </Button>
                </div>
              ))}
              <Button
                variant="secondary"
                size="sm"
                className="w-fit"
                onClick={() =>
                  setSecretHeaders([...secretHeaders, { name: "", value: "" }])
                }
              >
                <Plus className="size-3.5" aria-hidden />{" "}
                {saved?.has_secrets
                  ? "Replace secret headers"
                  : "Add secret header"}
              </Button>
            </div>
          </div>
        </Panel>
      )}

      {save.isError && <ErrorNotice error={save.error} />}
      <div className="flex flex-wrap gap-2">
        <Button
          loading={save.isPending}
          disabled={!draft.name || draft.description.length < 5}
          onClick={() => save.mutate()}
        >
          {saved ? "Save changes" : "Create tool"}
        </Button>
        <Button variant="ghost" onClick={onDone}>
          {saved ? "Close" : "Cancel"}
        </Button>
        {saved && (
          <Button
            variant="danger"
            className="ml-auto"
            loading={remove.isPending}
            onClick={async () => {
              const { ok } = await confirm({
                title: `Delete ${saved.name}?`,
                body: saved.used_by.length
                  ? `Remove it from ${saved.used_by.join(", ")} first.`
                  : "Agents can no longer use it. This can't be undone.",
                confirmLabel: "Delete",
                tone: "danger",
              });
              if (ok) remove.mutate();
            }}
          >
            Delete
          </Button>
        )}
      </div>
      {remove.isError && <ErrorNotice error={remove.error} />}

      {saved && (
        <Panel
          title="Test it"
          description="Runs the tool for real with these values: a test row really is added to the sheet. Save changes first."
        >
          <div className="flex flex-col gap-3">
            {asked.map((name) => (
              <Field key={name} label={name}>
                {(p) => (
                  <Input
                    {...p}
                    value={testValues[name] ?? ""}
                    onChange={(e) =>
                      setTestValues({ ...testValues, [name]: e.target.value })
                    }
                  />
                )}
              </Field>
            ))}
            <Button
              variant="secondary"
              className="w-fit"
              loading={test.isPending}
              onClick={() => test.mutate()}
            >
              Run test
            </Button>
            {test.isError && <ErrorNotice error={test.error} />}
            {test.data && (
              <div
                className={`rounded-lg px-3 py-2 text-[13px] ${test.data.ok ? "bg-success-soft" : "bg-danger-soft text-danger"}`}
                role="status"
              >
                <p className="font-medium">
                  {test.data.ok ? "It worked" : String(test.data.error)}
                </p>
                {Boolean(test.data.ok) && (
                  <pre className="mt-1 overflow-x-auto font-mono text-[12px] whitespace-pre-wrap text-ink">
                    {JSON.stringify(test.data, null, 2)}
                  </pre>
                )}
              </div>
            )}
          </div>
        </Panel>
      )}
    </div>
  );
}

export function ToolsView() {
  const { data: me } = useMe();
  const canEdit = me?.role === "admin";
  const [editing, setEditing] = useState<ToolDef | "new" | null>(null);
  const tools = useQuery({
    queryKey: ["tools"],
    queryFn: () => api<ToolDef[]>("/tools"),
  });

  if (editing) {
    return (
      <>
        <PageHeader
          title={editing === "new" ? "New tool" : editing.name}
          description="A tool is an action an agent can take during a call."
        />
        <ToolForm
          key={editing === "new" ? "new" : editing.id}
          tool={editing === "new" ? null : editing}
          onDone={() => setEditing(null)}
        />
      </>
    );
  }
  return (
    <>
      <PageHeader
        title="Tools"
        description="Actions agents can take during a call, like saving a promised payment date to your Google Sheet. Pick each agent's tools in its editor."
        actions={
          canEdit ? (
            <Button onClick={() => setEditing("new")}>
              <Plus className="size-4" aria-hidden /> New tool
            </Button>
          ) : undefined
        }
      />
      {tools.isPending ? (
        <Skeleton className="h-40 rounded-xl" />
      ) : tools.isError ? (
        <ErrorNotice error={tools.error} onRetry={() => tools.refetch()} />
      ) : tools.data.length === 0 ? (
        <EmptyState
          icon={Wrench}
          title="No tools yet"
          action={
            canEdit ? (
              <Button onClick={() => setEditing("new")}>
                Create your first tool
              </Button>
            ) : undefined
          }
        >
          Describe a tool in plain words and the AI sets it up: add a row to a
          Google Sheet, look up a customer, or call your CRM.
        </EmptyState>
      ) : (
        <ul className="divide-y divide-line rounded-xl border border-line">
          {tools.data.map((t) => (
            <li key={t.id}>
              <button
                type="button"
                disabled={!canEdit}
                onClick={() => setEditing(t)}
                className="flex w-full flex-wrap items-center gap-x-4 gap-y-2 px-5 py-4 text-left transition-colors hover:bg-rail disabled:cursor-default"
              >
                {t.type === "google_sheets" ? (
                  <FileSpreadsheet className="size-5 text-muted" aria-hidden />
                ) : t.type === "database" ? (
                  <Database className="size-5 text-muted" aria-hidden />
                ) : (
                  <Globe className="size-5 text-muted" aria-hidden />
                )}
                <div className="min-w-0 flex-1">
                  <p className="font-mono text-[13px] font-medium">{t.name}</p>
                  <p className="truncate text-[13px] text-muted">
                    {t.description}
                  </p>
                </div>
                <span className="text-2xs text-muted">
                  {TYPE_LABEL[t.type]} ·{" "}
                  {t.used_by.length
                    ? `used by ${t.used_by.join(", ")}`
                    : "not used yet"}{" "}
                  · {relativeTime(t.updated_at).toLowerCase()}
                </span>
                {!t.is_active && <Badge>Off</Badge>}
                {!t.has_secrets && t.type === "google_sheets" && (
                  <Badge tone="warning">Needs key</Badge>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
