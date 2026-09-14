/* Shared harness for component/route tests.
 *
 * Each test stubs global fetch with a (method, pathname) router, clears
 * session storage, and renders the real provider stack. No production mock
 * data: stubs return just enough of the backend contract for the case.
 */

import { render, type RenderResult } from "@testing-library/react";
import { RouterProvider } from "react-router-dom";
import { beforeEach, vi } from "vitest";
import { AppProviders } from "../app/providers";
import { createTestRouter } from "../app/router";
import { configureApiClient } from "../api/client";

export type FetchStub = (method: string, pathname: string, init: RequestInit) => Response | null;

export function jsonResponse(status: number, payload: unknown): Response {
  return new Response(payload === null ? "" : JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function installFetchStub(stub: FetchStub): void {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init = {}) => {
    const raw =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    const url = new URL(raw);
    const matched = stub(init.method ?? "GET", url.pathname, init);
    if (matched) return matched;
    return jsonResponse(404, { detail: "not found in test stub" });
  });
}

export function renderAtRoute(path: string, stub: FetchStub): RenderResult {
  installFetchStub(stub);
  configureApiClient({ baseUrl: "http://test.local" });
  return render(
    <AppProviders>
      <RouterProvider router={createTestRouter(path)} />
    </AppProviders>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  try {
    sessionStorage.clear();
  } catch {
    /* ignore */
  }
  // Reset all client state between test files sharing a vitest worker.
  // Passing null (not undefined) explicitly clears the auth/events slots.
  configureApiClient({
    baseUrl: "http://test.local",
    authProvider: null,
    events: null,
  });
});
