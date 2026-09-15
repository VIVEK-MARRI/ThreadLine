import { expect, test } from "@playwright/test";
import {
  expectNoSevereBrowserIssues,
  loginViaApi,
  organisationId,
  resolveEntityId,
  selectOrganisation,
  trackBrowserIssues,
} from "./helpers";

/* Console, network, and performance smoke audit: key routes must load with
 * no page errors, no failed requests, no console errors, and a bounded
 * number of API calls — with point lookups only where a detail page needs
 * its own record.
 */

test("key routes load quietly with bounded API traffic", async ({ page, request }) => {
  const issues = trackBrowserIssues(page);
  const token = await loginViaApi(page, request);
  const orgA = await organisationId(request, token, "e2e-alpha");
  const gatewayId = await resolveEntityId(request, token, orgA, "e2e payment gateway");

  await page.goto("/app/dashboard");
  await expect(
    page.getByRole("heading", { name: /good (morning|afternoon|evening)/i }),
  ).toBeVisible();
  await selectOrganisation(page, "E2E Alpha Org");
  await page.goto("/app/meetings/e2e-alpha-launch");
  await expect(page.getByRole("heading", { name: "E2E Alpha Launch Review" })).toBeVisible();
  await page.goto(`/app/entities/${gatewayId}`);
  await expect(page.getByRole("heading", { name: "e2e payment gateway" })).toBeVisible();
  await page.goto("/app/intelligence");
  await expect(page.getByRole("heading", { name: "Intelligence" })).toBeVisible();

  expectNoSevereBrowserIssues(issues);
  // Bounded fan-out: four rich routes must not explode into hundreds of calls.
  expect(issues.apiCalls.length).toBeLessThan(120);
  const pointLookups = issues.apiCalls.filter((call) =>
    /^GET \/api\/v1\/(meetings|entities)\/[^/]+$/.test(call),
  );
  // Detail pages fetch their own record; list and intelligence pages join
  // names from already-loaded collections instead.
  expect(pointLookups.length).toBeLessThanOrEqual(12);
});
