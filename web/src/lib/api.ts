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

export type Role = "admin" | "viewer";

export interface User {
  id: string;
  email: string;
  name: string;
  role: Role;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface Overview {
  calls_today: number;
  calls_7d: number;
  completed_7d: number;
  connect_rate_7d: number | null;
  avg_duration_seconds_7d: number | null;
  cost_7d: number;
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
  verified_at: string | null;
  last_error: string | null;
  answer_url: string;
  status_url: string;
  numbers: number;
}

export interface Integrations {
  ai: AIProviderStatus[];
  telephony: TelephonyAccount[];
  telephony_providers: string[];
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
  system_prompt: string;
  greetings: Record<string, string>;
  default_language: string;
  languages: string[];
  state_languages: Record<string, string>;
  announcement: { enabled: boolean; text: string; translations: Record<string, string> };
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
  summary: string | null;
  extracted: Record<string, unknown>;
  latency: Record<string, LatencySummary>;
  cost_breakdown: { usage?: Record<string, number> };
  error: string | null;
  events: CallEvent[];
}
