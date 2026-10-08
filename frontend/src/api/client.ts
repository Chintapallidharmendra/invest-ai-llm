/**
 * The only fetch wrapper (ADR-006, ADR-008). Every API call goes through here.
 *
 * - Cookies ride along (`credentials: 'include'`); the HttpOnly session cookie is never
 *   read by JS and nothing auth-related is kept in localStorage.
 * - Non-GET requests carry `X-CSRF-Token`, copied from the JS-readable `__Host-csrf`
 *   cookie (double submit). A missing cookie sends no header and the server rejects it.
 * - Non-2xx responses throw a {@link ProblemError}; a 401 also triggers the login redirect.
 */

import { CORRELATION_HEADER, ProblemError, problemFromResponse } from "./problem";

export const API_BASE = "/api/v1";
export const CSRF_COOKIE = "__Host-csrf";
export const CSRF_HEADER = "X-CSRF-Token";
export const LOGIN_PATH = "/login";

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export type QueryValue = string | number | boolean | null | undefined;

export interface RequestOptions extends Omit<RequestInit, "body" | "headers" | "method"> {
  method?: string;
  /** Plain values are sent as JSON; FormData, Blob, URLSearchParams and strings as-is. */
  body?: unknown;
  headers?: HeadersInit;
  query?: Record<string, QueryValue | readonly QueryValue[]>;
  /** Set false where a 401 is an expected answer (e.g. the login form). */
  redirectOn401?: boolean;
}

export function readCookie(name: string): string | undefined {
  if (typeof document === "undefined") return undefined;
  for (const part of document.cookie.split(";")) {
    const eq = part.indexOf("=");
    if (eq === -1) continue;
    if (part.slice(0, eq).trim() === name) {
      const raw = part.slice(eq + 1).trim();
      try {
        return decodeURIComponent(raw);
      } catch {
        return raw;
      }
    }
  }
  return undefined;
}

export function redirectToLogin(): void {
  const { pathname, search, hash } = window.location;
  if (pathname === LOGIN_PATH) return;
  const next = encodeURIComponent(`${pathname}${search}${hash}`);
  window.location.assign(`${LOGIN_PATH}?next=${next}`);
}

let unauthorizedHandler: () => void = redirectToLogin;

/** The app swaps in a router-based redirect; tests swap in a spy. Returns the previous one. */
export function setUnauthorizedHandler(handler: () => void): () => void {
  const previous = unauthorizedHandler;
  unauthorizedHandler = handler;
  return previous;
}

export function apiUrl(path: string, query?: RequestOptions["query"]): string {
  const base = path.startsWith(`${API_BASE}/`) ? path : `${API_BASE}${path.startsWith("/") ? "" : "/"}${path}`;
  if (!query) return base;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    const values: readonly QueryValue[] = Array.isArray(value) ? value : [value as QueryValue];
    for (const v of values) {
      if (v !== undefined && v !== null) params.append(key, String(v));
    }
  }
  const qs = params.toString();
  return qs ? `${base}${base.includes("?") ? "&" : "?"}${qs}` : base;
}

function isPassThroughBody(body: unknown): body is BodyInit {
  return (
    typeof body === "string" ||
    body instanceof FormData ||
    body instanceof Blob ||
    body instanceof URLSearchParams ||
    body instanceof ArrayBuffer ||
    body instanceof ReadableStream
  );
}

/**
 * Send a request and return the raw 2xx Response (used directly for SSE streams and
 * downloads). Throws ProblemError otherwise; network failures become status 0.
 */
export async function apiFetch(path: string, options: RequestOptions = {}): Promise<Response> {
  const { body, headers: headersInit, query, redirectOn401 = true, method: rawMethod, ...init } = options;
  const method = (rawMethod ?? (body === undefined ? "GET" : "POST")).toUpperCase();
  const headers = new Headers(headersInit);
  if (!headers.has("Accept")) headers.set("Accept", "application/json, application/problem+json");

  let payload: BodyInit | undefined;
  if (body !== undefined) {
    if (isPassThroughBody(body)) {
      payload = body;
    } else {
      payload = JSON.stringify(body);
      if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    }
  }

  if (!SAFE_METHODS.has(method)) {
    const token = readCookie(CSRF_COOKIE);
    if (token) headers.set(CSRF_HEADER, token);
  }

  let response: Response;
  try {
    response = await fetch(apiUrl(path, query), {
      ...init,
      method,
      headers,
      body: payload ?? null,
      credentials: "include",
    });
  } catch (error) {
    if ((error as { name?: unknown } | null)?.name === "AbortError") throw error;
    throw new ProblemError({
      status: 0,
      code: "network_error",
      title: "The service could not be reached",
    });
  }

  if (response.ok) return response;

  const problem = await problemFromResponse(response);
  if (response.status === 401 && redirectOn401) unauthorizedHandler();
  throw problem;
}

/** JSON request; resolves to the parsed body, or undefined for 204 / empty bodies. */
export async function apiJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await apiFetch(path, options);
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ProblemError({
      status: response.status,
      code: "invalid_response",
      title: "The service sent an unreadable response",
      correlationId: response.headers.get(CORRELATION_HEADER) ?? undefined,
    });
  }
}

type BodylessOptions = Omit<RequestOptions, "method" | "body">;

export const api = {
  get: <T>(path: string, options?: BodylessOptions) => apiJson<T>(path, { ...options, method: "GET" }),
  delete: <T = void>(path: string, options?: BodylessOptions) =>
    apiJson<T>(path, { ...options, method: "DELETE" }),
  post: <T>(path: string, body?: unknown, options?: BodylessOptions) =>
    apiJson<T>(path, { ...options, method: "POST", body }),
  put: <T>(path: string, body?: unknown, options?: BodylessOptions) =>
    apiJson<T>(path, { ...options, method: "PUT", body }),
  patch: <T>(path: string, body?: unknown, options?: BodylessOptions) =>
    apiJson<T>(path, { ...options, method: "PATCH", body }),
};
