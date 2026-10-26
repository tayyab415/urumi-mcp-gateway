/** The dashboard's only way to talk to the gateway and the chat service.
 *
 *  Sessions ride in an HttpOnly cookie. Every write carries `X-Urumi-CSRF: 1`,
 *  which the gateway requires. A 401 on any call means the session is gone, so
 *  the app is told once and shows the sign-in screen. */

export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: unknown) {
    super(message);
  }
}

export const UNAUTHORIZED_EVENT = "urumi:unauthorized";

async function request<T>(method: string, path: string, body?: unknown, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-Urumi-CSRF"] = "1";
  const res = await fetch(path, { method, headers, credentials: "same-origin", body: body === undefined ? undefined : JSON.stringify(body), ...init });
  if (res.status === 401 && !path.startsWith("/api/auth/login") && !path.startsWith("/api/invites/")) {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const detail = (data as { detail?: unknown; error?: unknown } | null) ?? {};
    const message =
      typeof detail.detail === "string" ? detail.detail
      : typeof detail.error === "string" ? detail.error
      : Array.isArray(detail.detail) ? (detail.detail as { msg?: string }[]).map((d) => d.msg).filter(Boolean).join(". ")
      : `${res.status} ${res.statusText}`;
    throw new ApiError(res.status, message, data);
  }
  return data as T;
}

export const http = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

// ------------------------------------------------------------------ types

export type Role = "admin" | "member";

export interface User {
  id: number;
  email: string;
  name: string;
  role: Role;
  active: boolean;
  created_at: string | null;
  active_keys?: number | null;
}

export interface Me extends User {
  mcp_url: string;
}

export type ServerHealth = "ok" | "down" | "auth_failed" | "unknown" | string;

export interface Server {
  id: number;
  slug: string;
  name: string;
  url: string;
  enabled: boolean;
  auth_type: "none" | "bearer" | "header";
  auth_header: string | null;
  has_credential: boolean;
  timeout_seconds: number;
  forward_identity: boolean;
  status: "disabled" | ServerHealth;
  health: ServerHealth;
  last_error: string | null;
  last_checked_at: string | null;
  last_ok_at: string | null;
  latency_ms: number | null;
  protocol_version: string | null;
  tool_count: number;
}

export interface ServerTool {
  name: string;
  public_name: string;
  title: string | null;
  description: string;
  annotations: Record<string, unknown>;
  allowed_roles: Role[];
  present: boolean;
  input_schema: Record<string, unknown> | null;
  /** Tool pinning: hidden and refused until an admin approves the current definition. */
  pending_review: boolean;
  hidden_characters: boolean;
  approved_definition: ToolDefinition | null;
  definition: ToolDefinition;
}

export interface ToolDefinition {
  title: string | null;
  description: string;
  input_schema: Record<string, unknown> | null;
  output_schema: Record<string, unknown> | null;
  annotations: Record<string, unknown> | null;
}

export interface MyTool {
  public_name: string;
  server: string;
  server_slug: string;
  server_status: ServerHealth;
  title: string | null;
  description: string;
  read_only: boolean;
}

export interface ApiKey {
  id: number;
  name: string;
  prefix: string;
  user_email: string;
  created_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface NewKey {
  id: number;
  name: string;
  prefix: string;
  key: string;
  mcp_url: string;
}

export interface Invite {
  id: number;
  email: string;
  role: Role;
  expires_at: string | null;
  invite_url?: string;
}

export type Outcome = "ok" | "denied" | "disabled" | "unknown_tool" | "unreachable" | "timeout" | "auth_failed" | "protocol" | "tool_error" | "aborted" | string;

export interface AuditEvent {
  id: number;
  started_at: string | null;
  user_id: number;
  user_email: string;
  via: "mcp" | "chat";
  api_key_prefix: string | null;
  server: string | null;
  tool: string | null;
  public_name: string;
  arguments: string;
  outcome: Outcome;
  error: string | null;
  duration_ms: number | null;
  chat_id: string | null;
}

export interface AuditSummary {
  since: string;
  servers: { slug: string; calls: number; errors: number; p50_ms: number | null; p95_ms: number | null }[];
  per_hour: { hour: string; calls: number; errors: number }[];
  slowest_tools: { public_name: string; calls: number; p95_ms: number | null }[];
}

export interface ServerDraft {
  slug: string;
  name: string;
  url: string;
  auth_type: "none" | "bearer" | "header";
  auth_header: string | null;
  credential: string | null;
  timeout_seconds: number;
  forward_identity: boolean;
  enabled: boolean;
}

export type TestResult =
  | { ok: true; latency_ms: number; protocol_version: string; tools: { name: string; public_name: string; title: string | null; read_only: boolean }[]; collisions: { name: string; servers: string[] }[] }
  | { ok: false; kind: "unreachable" | "timeout" | "auth_failed" | "protocol"; message: string };

export interface ChatMeta {
  model: string;
  model_label: string;
  approval_timeout_seconds: number;
}

// -------------------------------------------------------------- endpoints

const qs = (params: Record<string, string | number | null | undefined>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
};

export type StorePhase = "Provisioning" | "Ready" | "Failed" | "Deleting";

export interface Store {
  slug: string;
  name: string;
  namespace: string;
  owner_id: number;
  owner_email: string;
  phase: StorePhase;
  reason: string;
  url: string;
  admin_url: string;
  mcp_url: string;
  created_at: string | null;
  ready_at: string | null;
  deadline: string | null;
  server_slug: string;
  registered: boolean;
  server_status: string | null;
  mine: boolean;
  can_delete: boolean;
}

export const api = {
  stores: {
    list: () => http.get<{ stores: Store[] }>("/api/stores"),
    create: (slug: string, name: string) => http.post<Store>("/api/stores", { slug, name }),
    remove: (slug: string) => http.del<Store>(`/api/stores/${encodeURIComponent(slug)}`),
  },
  auth: {
    me: () => http.get<Me>("/api/auth/me"),
    login: (email: string, password: string) => http.post<User>("/api/auth/login", { email, password }),
    logout: () => http.post<{ ok: boolean }>("/api/auth/logout"),
    chatToken: () => http.post<{ token: string; chat_url: string }>("/api/auth/chat-token"),
  },
  servers: {
    list: () => http.get<Server[]>("/api/servers"),
    create: (draft: ServerDraft) => http.post<Server>("/api/servers", draft),
    update: (id: number, patch: Partial<ServerDraft> & { clear_credential?: boolean }) => http.patch<Server>(`/api/servers/${id}`, patch),
    remove: (id: number) => http.del<{ ok: boolean }>(`/api/servers/${id}`),
    refresh: (id: number) => http.post<Server>(`/api/servers/${id}/refresh`),
    tools: (id: number) => http.get<ServerTool[]>(`/api/servers/${id}/tools`),
    approveTool: (id: number, tool: string) => http.post<ServerTool>(`/api/servers/${id}/tools/${encodeURIComponent(tool)}/approve`),
    setToolRoles: (id: number, tool: string, allowed_roles: Role[]) => http.patch<ServerTool>(`/api/servers/${id}/tools/${encodeURIComponent(tool)}`, { allowed_roles }),
    test: (draft: Partial<ServerDraft> & { server_id?: number | null }) => http.post<TestResult>("/api/servers/test", draft),
  },
  tools: { mine: () => http.get<MyTool[]>("/api/tools") },
  users: {
    list: () => http.get<User[]>("/api/users"),
    update: (id: number, patch: { role?: Role; active?: boolean }) => http.patch<User>(`/api/users/${id}`, patch),
  },
  invites: {
    list: () => http.get<Invite[]>("/api/invites"),
    create: (email: string, role: Role) => http.post<Invite & { invite_url: string }>("/api/invites", { email, role }),
    revoke: (id: number) => http.del<{ ok: boolean }>(`/api/invites/${id}`),
    lookup: (token: string) => http.get<{ email: string; role: Role }>(`/api/invites/lookup/${encodeURIComponent(token)}`),
    accept: (token: string, name: string, password: string) => http.post<User>(`/api/invites/accept/${encodeURIComponent(token)}`, { name, password }),
  },
  keys: {
    list: (all = false) => http.get<ApiKey[]>(`/api/keys${all ? "?all=true" : ""}`),
    create: (name: string) => http.post<NewKey>("/api/keys", { name }),
    revoke: (id: number) => http.del<{ ok: boolean }>(`/api/keys/${id}`),
  },
  audit: {
    list: (params: { user_id?: number | null; server?: string | null; outcome?: string | null; via?: string | null; before_id?: number | null; after_id?: number | null; chat_id?: string | null; limit?: number }) =>
      http.get<AuditEvent[]>(`/api/audit${qs(params)}`),
    summary: (hours = 24) => http.get<AuditSummary>(`/api/audit/summary?hours=${hours}`),
  },
  chat: {
    meta: () => http.get<ChatMeta>("/api/chat/meta"),
    approve: (token: string, conversation_id: string, request_id: string, decision: "approve" | "deny") =>
      request<{ ok: boolean }>("POST", "/api/chat/approve", { conversation_id, request_id, decision }, { headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" } }),
  },
};

/** Servers in registration order, so the WooCommerce server the platform ships with is first (the gateway sorts by slug). */
export const byRegistration = <T extends { id: number }>(list: T[]) => [...list].sort((a, b) => a.id - b.id);
