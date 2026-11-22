const TOKEN_KEY = "resonance.token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  code: string;
  status: number;
  details?: unknown;

  constructor(message: string, code: string, status: number, details?: unknown) {
    super(message);
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");

  const response = await fetch(path, { ...init, headers });
  if (response.status === 401) {
    setToken(null);
    onUnauthorized?.();
  }
  const text = await response.text();
  const payload = text ? safeParse(text) : null;
  if (!response.ok) {
    const error = (payload as any)?.error ?? {};
    throw new ApiError(error.message ?? `请求失败 (${response.status})`, error.code ?? "error", response.status, error.details);
  }
  return payload as T;
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

async function download(path: string, filename: string): Promise<void> {
  const token = getToken();
  const response = await fetch(path, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!response.ok) throw new ApiError("下载失败", "download_failed", response.status);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export const api = {
  config: () => request<any>("/api/config"),
  login: (email: string, password: string) =>
    request<{ access_token: string; refresh_token: string; user: any }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  register: (body: Record<string, unknown>) =>
    request<{ access_token: string; user: any }>("/api/auth/register", { method: "POST", body: JSON.stringify(body) }),
  me: () => request<any>("/api/auth/me"),
  profile: () => request<any>("/api/me/profile"),
  transparency: () => request<any>("/api/me/transparency"),
  borrowPrompt: () => request<any>("/api/onboarding/borrow-prompt"),
  anchor: (body: Record<string, unknown>) =>
    request<any>("/api/onboarding/anchor", { method: "POST", body: JSON.stringify(body) }),
  borrow: (text: string, consent: boolean) =>
    request<any>("/api/onboarding/borrow", {
      method: "POST",
      body: JSON.stringify({ text, sandbox_consent: consent }),
    }),
  narrative: (narrative: string, consent: boolean) =>
    request<any>("/api/onboarding/narrative", {
      method: "POST",
      body: JSON.stringify({ narrative, sandbox_consent: consent }),
    }),
  preview: (topics: string[]) =>
    request<any>("/api/onboarding/preview", { method: "POST", body: JSON.stringify({ topics }) }),
  addCredential: (kind: string, subject: string) =>
    request<any>("/api/me/credentials", { method: "POST", body: JSON.stringify({ kind, subject }) }),
  wipeVault: () => request<any>("/api/me/vault", { method: "DELETE" }),
  cards: () => request<{ cards: any[]; quota: any }>("/api/cards"),
  actCard: (id: string, action: string, slotId?: string) =>
    request<any>(`/api/cards/${id}/act`, {
      method: "POST",
      body: JSON.stringify({ action, slot_id: slotId ?? null }),
    }),
  groups: () => request<{ groups: any[] }>("/api/groups"),
  joinGroup: (id: string) => request<any>(`/api/groups/${id}/join`, { method: "POST" }),
  leaveGroup: (id: string) => request<any>(`/api/groups/${id}/leave`, { method: "POST" }),
  proximityVenues: () => request<any>("/api/proximity/venues"),
  checkin: (sessionRef: string, visible: boolean) =>
    request<any>("/api/proximity/checkin", {
      method: "POST",
      body: JSON.stringify({ session_ref: sessionRef, visible }),
    }),
  radar: () => request<any>("/api/proximity/radar"),
  ping: (ref: string) => request<any>("/api/proximity/ping", { method: "POST", body: JSON.stringify({ ref }) }),
  checkout: () => request<any>("/api/proximity/checkout", { method: "POST" }),
  gatekeeperPolicy: () => request<any>("/api/gatekeeper/policy"),
  updateGatekeeperPolicy: (body: Record<string, unknown>) =>
    request<any>("/api/gatekeeper/policy", { method: "PUT", body: JSON.stringify(body) }),
  pitch: (body: Record<string, unknown>) =>
    request<any>("/api/gatekeeper/requests", { method: "POST", body: JSON.stringify(body) }),
  gatekeeperIncoming: () => request<{ requests: any[] }>("/api/gatekeeper/incoming"),
  gatekeeperOutgoing: () => request<{ requests: any[] }>("/api/gatekeeper/outgoing"),
  runs: () => request<{ runs: any[] }>("/api/runs"),
  runDetail: (id: string) => request<any>(`/api/runs/${id}`),
  triggerRun: (body: Record<string, unknown>) =>
    request<any>("/api/runs", { method: "POST", body: JSON.stringify(body) }),
  stats: () => request<any>("/api/admin/stats"),
  cost: () => request<any>("/api/admin/cost"),
  auditSessions: () => request<{ sessions: any[]; guarantee: string }>("/api/admin/audit"),
  bindings: () => request<{ bindings: any[] }>("/api/harness/bindings"),
  createBinding: (kind: string, label: string) =>
    request<any>("/api/harness/bindings", { method: "POST", body: JSON.stringify({ kind, label }) }),
  revokeBinding: (id: string) => request<any>(`/api/harness/bindings/${id}`, { method: "DELETE" }),
  downloadMarkdown: () => download("/api/export/markdown", "resonance-dossier.md"),
  downloadAgentCard: () => download("/api/export/agent-card", "agent-card.json"),
};

export type Api = typeof api;
