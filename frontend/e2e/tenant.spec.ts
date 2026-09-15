import { expect, test } from "@playwright/test";
import {
  authHeaders,
  BACKEND_URL,
  loginViaApi,
  organisationId,
  resolveEntityId,
  trackBrowserIssues,
  expectNoSevereBrowserIssues,
} from "./helpers";

/* Tenant isolation in a real browser: switching organisations must swap
 * content, reset query state, clear stale answers, and never leak the other
 * tenant's objects through direct routes.
 */

test("organisation switch swaps content, resets filters, and clears answers", async ({
  page,
  request,
}) => {
  const issues = trackBrowserIssues(page);
  const token = await loginViaApi(page, request);
  void token;

  await page.goto("/app/entities");
  await expect(page.getByRole("heading", { name: "Entities" })).toBeVisible();
  await page.getByRole("button", { name: "Switch organisation" }).click();
  await page.getByRole("menuitem", { name: "E2E Alpha Org" }).click();
  await expect(page.getByRole("link", { name: "e2e payment gateway" })).toBeVisible();
  await page.getByLabel("Search entities").fill("payment");
  await expect(page.getByRole("link", { name: "e2e payment gateway" })).toBeVisible();

  await page.goto("/app/ask");
  await page.getByLabel("Your question").fill("What is blocking e2e payment gateway?");
  await page.getByRole("button", { name: "Ask ThreadLine" }).click();
  await expect(page.getByRole("heading", { name: "Answer" })).toBeVisible();

  await page.getByRole("button", { name: "Switch organisation" }).click();
  await page.getByRole("menuitem", { name: "E2E Beta Org" }).click();
  await page.goto("/app/entities");
  await expect(page.getByRole("link", { name: "e2e checkout flow" })).toBeVisible();
  await expect(page.getByRole("link", { name: "e2e payment gateway" })).toHaveCount(0);

  await page.goto("/app/entities");
  await expect(page.getByLabel("Search entities")).toHaveValue("");
  await expect(page.getByRole("link", { name: "e2e checkout flow" })).toBeVisible();

  await page.goto("/app/ask");
  await expect(page.getByLabel("Your question")).toHaveValue("");
  await expect(page.getByRole("heading", { name: "Answer" })).toHaveCount(0);
  expectNoSevereBrowserIssues(issues);
});

test("direct cross-tenant route is safely unavailable", async ({ page, request }) => {
  const issues = trackBrowserIssues(page);
  const token = await loginViaApi(page, request);
  const orgA = await organisationId(request, token, "e2e-alpha");
  const gatewayId = await resolveEntityId(request, token, orgA, "e2e payment gateway");

  await page.goto("/app/entities");
  await expect(page.getByRole("heading", { name: "Entities" })).toBeVisible();
  await page.getByRole("button", { name: "Switch organisation" }).click();
  await page.getByRole("menuitem", { name: "E2E Alpha Org" }).click();
  await expect(page.getByRole("link", { name: "e2e payment gateway" })).toBeVisible();
  await page.getByRole("button", { name: "Switch organisation" }).click();
  await page.getByRole("menuitem", { name: "E2E Beta Org" }).click();
  await expect(page.getByRole("link", { name: "e2e checkout flow" })).toBeVisible();

  await page.goto(`/app/entities/${gatewayId}`);
  await expect(page.getByText("This entity isn't available")).toBeVisible();

  // The Beta tenant's own objects still resolve.
  const betaResponse = await request.get(`${BACKEND_URL}/api/v1/entities`, {
    headers: authHeaders(token, await organisationId(request, token, "e2e-beta")),
  });
  expect(betaResponse.ok()).toBe(true);
  expectNoSevereBrowserIssues(issues);
});
