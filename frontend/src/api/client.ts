/* Centralized ThreadLine API client.
 *
 * The ONLY place that talks HTTP. Feature modules (auth.ts, meetings.ts,
 * ...) build typed calls on top of `request()`. Components never call
 * fetch() directly.
 *
 * - Base URL: same-origin `/api/v1` in dev (Vite proxies to FastAPI), or
 *   `VITE_API_BASE_URL` for other environments.
 * - Auth: bearer token + organisation scope headers, supplied by the auth
 *   layer through `setAuthProvider` (no imports from React code → no cycles).
 * - Errors: every failure becomes a typed `ApiError` (see types/api.ts).
 *   401 additionally notifies the registered handler so the app can sign
 *   the user out exactly once. Tokens are never logged.
 */

import { ApiError, type ApiErrorKind } from "../types/api";

export interface AuthProvider {
  getToken: () => string | null;
  getOrganisationId: () => string | null;
}

export interface ClientEvents {
  onUnauthorized: () => void;
}

const state: {
  baseUrl: string;
  auth: AuthProvider | null;
  events: ClientEvents | null;
  unauthorizedNotified: boolean;
} = {
  baseUrl: "",
  auth: null,
  events: null,
  unauthorizedNotified: false,
};

export function configureApiClient(options: {
  baseUrl?: string;
  authProvider?: AuthProvider | null;
  events?: ClientEvents | null;
}): void {
  if (options.baseUrl !== undefined) state.baseUrl = options.baseUrl;
  if (options.authProvider !== undefined) state.auth = options.authProvider;
  if (options.events !== undefined) {
    state.events = options.events;
    state.unauthorizedNotified = false;
  }
}

/** Base URL for tests / integration harnesses to override per-run. */
export function getApiBaseUrl(): string {
  if (typeof import.meta !== "undefined" && import.meta.env?.VITE_API_BASE_URL) {
    return import.meta.env.VITE_API_BASE_URL as string;
  }
  return state.baseUrl;
}

function kindForStatus(status: number): ApiErrorKind {
  if (status === 401) return "unauthorized";
  if (status === 403) return "forbidden";
  if (status === 404) return "not-found";
  if (status === 409) return "conflict";
  if (status === 422) return "validation";
  if (status === 429) return "rate-limited";
  if (status === 502 || status === 503) return "unavailable";
  return "unknown";
}

function fallbackMessage(kind: ApiErrorKind, status: number | null): string {
  if (kind === "unauthorized") return "Authentication is required.";
  if (kind === "forbidden") return "You don't have permission to do that.";
  if (kind === "not-found") return "The requested item was not found.";
  if (kind === "rate-limited") return "Too many attempts. Please try again later.";
  if (kind === "unavailable") return "The service is temporarily unavailable.";
  if (kind === "network") return "The request failed before reaching ThreadLine.";
  return status === null ? "The request failed." : `Request failed (${status}).`;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  /** Default true. The bootstrap endpoint passes false. */
  auth?: boolean;
  signal?: AbortSignal;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, query, auth = true, signal } = options;

  let url = `${getApiBaseUrl()}${path}`;
  if (query) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null) params.set(key, String(value));
    }
    const suffix = params.toString();
    if (suffix) url += `?${suffix}`;
  }

  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth && state.auth) {
    const token = state.auth.getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
    // Organisation scope is a hint only: the backend derives authority from
    // membership and rejects anything the caller cannot access.
    const organisationId = state.auth.getOrganisationId();
    if (organisationId) headers["X-Organisation-ID"] = organisationId;
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch {
    throw new ApiError({ kind: "network", status: null, message: fallbackMessage("network", null) });
  }

  if (response.status === 204) return undefined as T;

  let payload: unknown = null;
  try {
    const text = await response.text();
    payload = text ? (JSON.parse(text) as unknown) : null;
  } catch {
    payload = null;
  }

  if (response.ok) return payload as T;

  if (response.status === 401 && state.events && !state.unauthorizedNotified) {
    state.unauthorizedNotified = true;
    try {
      state.events.onUnauthorized();
    } catch {
      // Listener failures must never break error propagation.
    }
  }

  const kind = kindForStatus(response.status);
  let message = fallbackMessage(kind, response.status);
  let fields: Record<string, string[]> | undefined;
  if (payload !== null && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    if (typeof record["detail"] === "string" && record["detail"].length > 0) {
      message = record["detail"];
    } else if (Array.isArray(record["detail"])) {
      // FastAPI 422 envelope: [{loc, msg, type}, ...]
      fields = {};
      for (const item of record["detail"]) {
        if (item !== null && typeof item === "object") {
          const entry = item as { loc?: unknown; msg?: unknown };
          const location = Array.isArray(entry.loc) ? entry.loc.join(".") : "request";
          const text = typeof entry.msg === "string" ? entry.msg : "Invalid value.";
          (fields[location] ??= []).push(text);
        }
      }
      const first = Object.values(fields)[0]?.[0];
      if (first) message = first;
    }
  }
  throw new ApiError({ kind, status: response.status, message, fields });
}
