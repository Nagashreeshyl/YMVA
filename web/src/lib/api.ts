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
