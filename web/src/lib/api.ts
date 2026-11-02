import type { Perm } from "@/lib/permissions";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function detailMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  // FastAPI validation errors: [{ loc, msg }, ...]
  if (Array.isArray(detail) && detail.length > 0 && typeof detail[0]?.msg === "string") {
    return detail.map((d: { msg: string }) => d.msg.replace(/^Value error, /, "")).join(". ");
  }
  return fallback;
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      credentials: "same-origin",
      ...rest,
      headers: { ...(json !== undefined ? { "content-type": "application/json" } : {}), ...headers },
      body: json !== undefined ? JSON.stringify(json) : rest.body,
    });
  } catch {
    throw new ApiError(0, "Can't reach the YMVA server. Check your connection and try again.");
  }
  if (response.status === 401 && path !== "/auth/login" && typeof window !== "undefined") {
    // Session expired or revoked: clear the stale cookie so the sign-in page doesn't bounce back.
    await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" }).catch(() => {});
    // Full reload on purpose: drops every cached query from the old session.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign(`/login?next=${encodeURIComponent(window.location.pathname)}`);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(
      response.status,
      detailMessage(body?.detail, `Request failed (${response.status}). Please try again.`),
    );
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export type Role = "owner" | "admin" | "campaign_manager" | "caller" | "reviewer" | "viewer";

export interface User {
  id: string;
  email: string;
  name: string;
  role: Role;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
  permissions: Perm[];
}

export interface Overview {
  calls_today: number;
  calls_7d: number;
  completed_7d: number;
  connect_rate_7d: number | null;
  avg_duration_seconds_7d: number | null;
  /** null when the role hides costs. */
  cost_7d: number | null;
  /** USD per minute of talk time (AI + phone line); null with no calls yet. */
  cost_per_minute_7d: number | null;
  active_calls: number;
  agents_active: number;
  agents_total: number;
  providers_connected: number;
  phone_numbers: number;
  daily: { date: string; calls: number; completed: number }[];
  outcomes_7d: Record<string, number>;
  languages_7d: Record<string, number>;
}

export interface AuditLog {
  id: string;
  created_at: string;
  actor_label: string;
  action: string;
  entity_type: string;
  entity_id: string | null;
  summary: string;
  changes: Record<string, unknown>;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface AIProviderStatus {
  provider: "sarvam" | "google";
  label: string;
  purpose: string;
  key_url: string;
  configured: boolean;
  key_hint: string | null;
  verified_at: string | null;
  last_error: string | null;
}

export interface TelephonyAccount {
  id: string;
  provider: "plivo" | "vobiz";
  name: string;
  credential_hint: string;
  is_active: boolean;
  max_concurrent_calls: number;
  /** What the provider charges per minute, in rupees; null until the admin enters it. */
  cost_per_minute_inr: number | null;
  verified_at: string | null;
  last_error: string | null;
  answer_url: string;
  status_url: string;
  numbers: number;
}

export interface TelephonyProviderInfo {
  id: string;
  name: string;
  /** The boxes to fill in when connecting an account. */
  fields: { key: string; label: string; secret: boolean; hint: string; multiline: boolean }[];
  /** How to point the provider's numbers at YMVA. */
  setup: string;
}

export interface Integrations {
  ai: AIProviderStatus[];
  telephony: TelephonyAccount[];
  telephony_providers: TelephonyProviderInfo[];
  public_base_url: string;
  public_url_is_https: boolean;
}

export interface PhoneNumber {
  id: string;
  e164: string;
  label: string;
  provider_account_id: string;
  provider: string;
  provider_account_name: string;
  inbound_agent_id: string | null;
  inbound_agent_name: string | null;
  max_concurrent_calls: number;
  is_active: boolean;
  created_at: string;
}

export interface AgentVariable {
  name: string;
  description: string;
  example: string;
}

export interface AgentConfig {
  mode: "pipeline" | "gemini_live";
  speaking_style: "everyday_mix" | "pure";
  system_prompt: string;
  greetings: Record<string, string>;
  default_language: string;
  languages: string[];
  /** Missing on agents saved before the setting existed: they follow the caller. */
  follow_caller_language?: boolean;
  state_languages: Record<string, string>;
  announcement: { enabled: boolean; text: string; translations: Record<string, string> };
  /** Hand the call to a person when the caller asks (phone calls only): this number, or the
   * manager of the customer's branch (their "branch" detail; `phone` then covers unlisted ones). */
  transfer?: { enabled: boolean; to?: "number" | "branch_manager"; phone: string; when: string };
  /** Optional notes for {{names}} used in the text; values are given when calling. */
  variables: AgentVariable[];
  pipeline: {
    stt_provider: "sarvam";
    llm_provider: "google";
    llm_model: string;
    backup_llm_model: string;
    tts_provider: "sarvam";
    voice: string;
    speaking_pace: number;
    temperature: number;
  };
  gemini_live: { model: string; voice: string; temperature: number };
  limits: { max_duration_seconds: number; silence_timeout_seconds: number };
  atmosphere: { room_tone: boolean; typing_while_thinking: boolean; volume: number };
  /** Written down after each call: one outcome and any of these details the customer gave. */
  analysis: { outcomes: TrackedItem[]; details: TrackedItem[]; send_to: string[] };
  /** Names of tools from the Tools page the agent may use during calls. */
  tools: string[];
}

export interface TrackedItem {
  name: string;
  description: string;
}

export interface AgentSummary {
  id: string;
  name: string;
  description: string;
  status: "draft" | "active" | "paused" | "archived";
  mode: "pipeline" | "gemini_live";
  languages: string[];
  latest_version: number;
  published_version: number | null;
  has_unpublished_changes: boolean;
  updated_at: string;
}

export interface AgentDetail extends AgentSummary {
  /** The saved draft if there is one, otherwise the latest version. */
  config: AgentConfig;
  has_draft_changes: boolean;
  draft_updated_at: string | null;
  latest_version_id: string;
  published_version_id: string | null;
  versions: {
    id: string;
    version: number;
    note: string;
    source: string;
    created_at: string;
    created_by: string | null;
  }[];
}

export interface AssistantMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  /** What the assistant changed, in plain words; empty when it only answered. */
  changes: string[];
  can_undo: boolean;
  undone: boolean;
  created_at: string;
}

export interface AssistantTurn {
  messages: AssistantMessage[];
  agent: AgentDetail;
}

export interface ToolParam {
  name: string;
  description: string;
  required: boolean;
}

export interface SheetColumn {
  header: string;
  value: string;
}

export interface SheetsToolConfig {
  /** Missing on tools saved before Apps Script existed: those use a service account. */
  connection?: "service_account" | "apps_script";
  spreadsheet: string;
  sheet: string;
  action: "append_row" | "find_row";
  columns: SheetColumn[];
  match_column: string;
  match_value: string;
  /** Columns follow each agent's "Details to write down" (plus call time, phone, outcome, summary). */
  agent_details?: boolean;
}

export interface HttpToolConfig {
  method: "GET" | "POST" | "PUT" | "PATCH";
  url: string;
  headers: Record<string, string>;
  body: string;
}

export interface DatabaseToolConfig {
  query: string;
  mode: "read" | "write";
}

export interface McpToolConfig {
  server_url: string;
  /** The tool's name on the MCP server. */
  tool: string;
  arg_types: Record<string, string>;
}

/** A tool an MCP server offers, shaped for the tool form. */
export interface McpServerTool {
  tool: string;
  description: string;
  params: ToolParam[];
  arg_types: Record<string, string>;
}

export type ToolType = "google_sheets" | "http" | "database" | "mcp";

export interface ToolDef {
  id: string;
  name: string;
  description: string;
  type: ToolType;
  config: SheetsToolConfig | HttpToolConfig | DatabaseToolConfig | McpToolConfig;
  params: ToolParam[];
  has_secrets: boolean;
  /** The service account's email (share the sheet with it) or the secret header names. */
  secret_hint: string | null;
  is_active: boolean;
  used_by: string[];
  /** Still needed before it can run: the sheet "link", the API "address" or its "key". */
  missing: ("link" | "address" | "key")[];
  updated_at: string;
}

export interface AgentOptions {
  languages: { code: string; name: string; native: string }[];
  sarvam_voices: string[];
  gemini_live_voices: string[];
  llm_models: string[];
  gemini_live_models: string[];
  templates: { id: string; name: string }[];
}

export interface CallSummary {
  id: string;
  created_at: string;
  direction: "inbound" | "outbound" | "web";
  status: string;
  agent_id: string | null;
  agent_name: string | null;
  from_number: string | null;
  to_number: string | null;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  end_reason: string | null;
  outcome: string | null;
  summary: string | null;
  /** Estimated AI cost in USD (phone charges not included). */
  cost_total: number | null;
  languages: string[];
}

export interface CallEvent {
  seq: number;
  at: string;
  type: "user_turn" | "agent_turn" | "tool_call" | "tool_result" | "language_switch" | "status" | "error";
  language: string | null;
  content: string;
  data: Record<string, unknown>;
}

type LatencySummary = { p50_ms: number; p95_ms: number; count: number } | null;

export interface CallDetail extends CallSummary {
  provider: string | null;
  has_recording: boolean;
  variables: Record<string, string>;
  extracted: Record<string, string>;
  latency: Record<string, LatencySummary>;
  cost_breakdown: { usage?: Record<string, number>; cost_usd?: Record<string, number> };
  error: string | null;
  events: CallEvent[];
}

export type CampaignStatus = "draft" | "running" | "paused" | "completed";
export type ContactStatus = "pending" | "calling" | "retry" | "done" | "failed";

export interface CampaignInput {
  name: string;
  agent_id: string;
  phone_number_ids: string[];
  /** 0 = Monday … 6 = Sunday */
  days: number[];
  start_time: string;
  end_time: string;
  max_concurrent: number;
  max_attempts: number;
  retry_after_minutes: number;
}

export interface Campaign extends CampaignInput {
  id: string;
  agent_name: string | null;
  status: CampaignStatus;
  /** Why dialing is held up right now. */
  last_error: string | null;
  counts: Record<ContactStatus | "total", number>;
  outcomes: Record<string, number>;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface CampaignContact {
  id: string;
  phone: string;
  variables: Record<string, string>;
  status: ContactStatus;
  attempts: number;
  last_result: string | null;
  next_attempt_at: string | null;
  call_id: string | null;
  outcome: string | null;
  summary: string | null;
  extracted: Record<string, string>;
}

export interface ContactImportResult {
  added: number;
  duplicates: number;
  invalid: { row: number; value: string; reason: string }[];
  invalid_count: number;
  columns: string[];
}

/** A branch and its manager (Settings > Branches); transfers go to the customer's branch. */
export interface Branch {
  id: string;
  name: string;
  other_names: string[];
  manager_name: string;
  manager_phone: string;
  updated_at: string;
}
