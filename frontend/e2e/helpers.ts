import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { AxeBuilder } from "@axe-core/playwright";

/* Shared browser-validation helpers. Every assertion targets real rendered
 * state and real backend contracts; nothing here invents product data.
 */

export const BACKEND_URL = process.env.E2E_BACKEND_URL ?? "http://127.0.0.1:8000";
export const OWNER_EMAIL = "e2e.owner@example.com";
export const PASSWORD = "E2E-Strong-Password-1";
export const NOMEMBER_EMAIL = "e2e.nomember@example.com";

export function authHeaders(token: string, orgId?: string): Record<string, string> {
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  if (orgId) headers["X-Organisation-ID"] = orgId;
  return headers;
}

/** Authenticate against the real backend, then inject the real session token. */
export async function loginViaApi(
  page: Page,
  request: APIRequestContext,
  email: string = OWNER_EMAIL,
  password: string = PASSWORD,
): Promise<string> {
  const response = await request.post(`${BACKEND_URL}/api/v1/auth/login`, {
    data: { email, password },
  });
  expect(response.ok()).toBe(true);
  const token = ((await response.json()) as { access_token: string }).access_token;
  await page.addInitScript((sessionToken: string) => {
    sessionStorage.setItem("tl.session.token", sessionToken);
  }, token);
  return token;
}

/** Select an organisation through the real header switcher. */
export async function selectOrganisation(page: Page, name: string): Promise<void> {
  await page.getByRole("button", { name: "Switch organisation" }).click();
  await page.getByRole("menuitem", { name }).click();
}

/** Resolve a canonical entity id through the real entity registry. */
export async function resolveEntityId(
  request: APIRequestContext,
  token: string,
  orgId: string,
  canonicalName: string,
): Promise<string> {
  const response = await request.get(`${BACKEND_URL}/api/v1/entities`, {
    headers: authHeaders(token, orgId),
  });
  expect(response.ok()).toBe(true);
  const entities = (await response.json()) as Array<{
    entity_id: string;
    canonical_name: string;
  }>;
  const match = entities.find((entity) => entity.canonical_name === canonicalName);
  expect(match, `expected entity ${canonicalName}`).toBeDefined();
  return match!.entity_id;
}

export async function organisationId(
  request: APIRequestContext,
  token: string,
  slug: string,
): Promise<string> {
  const response = await request.get(`${BACKEND_URL}/api/v1/auth/me`, {
    headers: authHeaders(token),
  });
  expect(response.ok()).toBe(true);
  const me = (await response.json()) as {
    memberships: Array<{ organisation: { organisation_id: string; slug: string } }>;
  };
  const match = me.memberships.find((m) => m.organisation.slug === slug);
  expect(match, `expected membership in ${slug}`).toBeDefined();
  return match!.organisation.organisation_id;
}

export interface BrowserIssues {
  consoleErrors: string[];
  pageErrors: string[];
  failedRequests: string[];
  abortedRequests: string[];
  apiCalls: string[];
}

/** Records console errors, page errors, failed requests, and API traffic. */
export function trackBrowserIssues(page: Page): BrowserIssues {
  const issues: BrowserIssues = {
    consoleErrors: [],
    pageErrors: [],
    failedRequests: [],
    abortedRequests: [],
    apiCalls: [],
  };
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    // Chromium logs "Failed to load resource" for every HTTP error status,
    // including the 401/403/404/500 responses the app deliberately exercises
    // and renders as safe UI states. Those are covered by requestfailed (for
    // transport failures) and by the tests' own error-state assertions.
    if (message.text().startsWith("Failed to load resource:")) return;
    issues.consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => {
    issues.pageErrors.push(String(error));
  });
  page.on("requestfailed", (req) => {
    // Aborted requests are React Query cancelling in-flight fetches on
    // unmount, navigation, or organisation switch. That cancellation is
    // correct tenant hygiene, not a failure, so it is tracked separately.
    if (req.failure()?.errorText === "net::ERR_ABORTED") {
      issues.abortedRequests.push(`${req.method()} ${new URL(req.url()).pathname}`);
      return;
    }
    issues.failedRequests.push(`${req.method()} ${req.url()} ${req.failure()?.errorText}`);
  });
  page.on("request", (req) => {
    if (req.url().includes("/api/")) {
      issues.apiCalls.push(`${req.method()} ${new URL(req.url()).pathname}`);
    }
  });
  return issues;
}

export function expectNoSevereBrowserIssues(issues: BrowserIssues): void {
  expect(issues.pageErrors, `page errors: ${issues.pageErrors.join(" | ")}`).toEqual([]);
  expect(
    issues.failedRequests,
    `failed requests: ${issues.failedRequests.join(" | ")}`,
  ).toEqual([]);
  expect(
    issues.consoleErrors,
    `console errors: ${issues.consoleErrors.join(" | ")}`,
  ).toEqual([]);
}

export async function expectNoHorizontalOverflow(page: Page, url?: string): Promise<void> {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow, `horizontal overflow on ${url ?? page.url()}`).toBeLessThanOrEqual(1);
}

export async function expectNoAxeViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).analyze();
  const summary = results.violations.map(
    (violation) =>
      `${violation.id} (${violation.nodes.length} nodes): ${violation.help} :: ${violation.nodes
        .slice(0, 8)
        .map((node) => node.target.join(" "))
        .join(" || ")}`,
  );
  expect(summary).toEqual([]);
}
