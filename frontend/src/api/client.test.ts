import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../types/api";
import { configureApiClient, request } from "./client";

function jsonResponse(status: number, payload: unknown): Response {
  return new Response(payload === null ? "" : JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("api client", () => {
  beforeEach(() => {
    configureApiClient({
      baseUrl: "http://test.local",
      authProvider: {
        getToken: () => "token-abc",
        getOrganisationId: () => "org-1",
      },
      events: { onUnauthorized: () => undefined },
    });
    vi.restoreAllMocks();
  });

  it("attaches bearer token and organisation scope headers", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, { ok: true }));
    await request("/api/v1/entities");
    const [, init] = spy.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer token-abc");
    expect(headers["X-Organisation-ID"]).toBe("org-1");
  });

  it("omits auth headers for bootstrap calls", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(201, {}));
    await request("/api/v1/auth/bootstrap", {
      method: "POST",
      body: {},
      auth: false,
    });
    const [, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(init.headers).not.toHaveProperty("Authorization");
  });

  it.each([
    [401, "unauthorized", "invalid session"],
    [403, "forbidden", "no access"],
    [404, "not-found", "missing"],
    [409, "conflict", "duplicate"],
    [429, "rate-limited", "slow down"],
    [503, "unavailable", "try later"],
  ])("maps HTTP %i to %s with the backend detail", async (status, kind, detail) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(status, { detail }));
    const failure = await request("/x").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    const apiError = failure as ApiError;
    expect(apiError.kind).toBe(kind);
    expect(apiError.status).toBe(status);
    expect(apiError.message).toBe(detail);
  });

  it("parses FastAPI 422 envelopes into field errors", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(422, {
        detail: [{ loc: ["body", "email"], msg: "field required", type: "missing" }],
      }),
    );
    const failure = await request("/x", { method: "POST", body: {} }).catch(
      (error: unknown) => error,
    );
    const apiError = failure as ApiError;
    expect(apiError.kind).toBe("validation");
    expect(apiError.fields?.["body.email"]).toEqual(["field required"]);
  });

  it("maps fetch failures to network errors without leaking internals", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    const failure = await request("/x").catch((error: unknown) => error);
    const apiError = failure as ApiError;
    expect(apiError.kind).toBe("network");
    expect(apiError.message).not.toContain("Failed to fetch");
  });

  it("notifies unauthorized exactly once per configuration", async () => {
    const onUnauthorized = vi.fn();
    configureApiClient({ events: { onUnauthorized } });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(401, { detail: "nope" }));
    await request("/a").catch(() => undefined);
    await request("/b").catch(() => undefined);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("serializes query parameters and skips empty values", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, []));
    await request("/api/v1/entities", {
      query: { entity_type: "ISSUE", limit: undefined, q: null },
    });
    const [url] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://test.local/api/v1/entities?entity_type=ISSUE");
  });
});
