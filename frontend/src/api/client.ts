/** Minimal typed API client with automatic token refresh. */
const BASE = "/api/v1";

let accessToken: string | null = localStorage.getItem("cg_access");
let refreshToken: string | null = localStorage.getItem("cg_refresh");

export function setTokens(access: string | null, refresh: string | null) {
  accessToken = access;
  refreshToken = refresh;
  if (access) localStorage.setItem("cg_access", access);
  else localStorage.removeItem("cg_access");
  if (refresh) localStorage.setItem("cg_refresh", refresh);
  else localStorage.removeItem("cg_refresh");
}

export function getAccessToken() { return accessToken; }
export function isAuthenticated() { return !!accessToken; }

export class ApiError extends Error {
  code: string;
  status: number;
  details: Record<string, unknown>;
  constructor(status: number, code: string, message: string, details = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

async function tryRefresh(): Promise<boolean> {
  if (!refreshToken) return false;
  const r = await fetch(`${BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!r.ok) { setTokens(null, null); return false; }
  const body = await r.json();
  setTokens(body.access_token, body.refresh_token);
  return true;
}

export async function api<T>(path: string, options: RequestInit = {}, retried = false): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;
  const resp = await fetch(`${BASE}${path}`, { ...options, headers });
  if (resp.status === 401 && !retried && (await tryRefresh())) {
    return api<T>(path, options, true);
  }
  if (!resp.ok) {
    let err = { code: "HTTP_ERROR", message: resp.statusText, details: {} };
    try { err = (await resp.json()).error ?? err; } catch { /* ignore */ }
    if (resp.status === 401) setTokens(null, null);
    throw new ApiError(resp.status, err.code, err.message, err.details);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

export const get = <T,>(path: string) => api<T>(path);
export const post = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined });
export const patch = <T,>(path: string, body: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const put = <T,>(path: string, body: unknown) =>
  api<T>(path, { method: "PUT", body: JSON.stringify(body) });
export const del = <T,>(path: string) => api<T>(path, { method: "DELETE" });
