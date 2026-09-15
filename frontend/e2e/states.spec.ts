import { expect, test } from "@playwright/test";
import {
  expectNoSevereBrowserIssues,
  loginViaApi,
  selectOrganisation,
  trackBrowserIssues,
} from "./helpers";

/* Loading, failure, empty, forbidden, and not-found states against the real
 * backend. Delays reuse the real responses; aborts simulate transport
 * failure only where the UI must degrade and retry.
 */

test("delayed intelligence shows skeletons before real content", async ({ page, request }) => {
  await loginViaApi(page, request);
  await page.route("**/api/v1/attention", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 800));
    await route.continue();
  });
  await page.goto("/app/intelligence");
  await expect(page.getByRole("heading", { name: "Intelligence" })).toBeVisible();
  await selectOrganisation(page, "E2E Alpha Org");
  await expect(
    page.getByRole("status", { name: "Loading attention signals" }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "e2e payment gateway" }).first()).toBeVisible();
});

test("aborted changes fail independently and retry against the real backend", async ({
  page,
  request,
}) => {
  const issues = trackBrowserIssues(page);
  await loginViaApi(page, request);
  await page.route("**/api/v1/changes*", (route) => route.abort("failed"));
  await page.goto("/app/intelligence");
  await expect(page.getByRole("heading", { name: "Intelligence" })).toBeVisible();
  await selectOrganisation(page, "E2E Alpha Org");
  await expect(page.getByText("Couldn't load changes")).toBeVisible();
  await expect(
    page.getByRole("link", { name: "e2e payment gateway" }).first(),
  ).toBeVisible();

  await page.unrouteAll({ behavior: "wait" });
  const stream = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "Change stream" }) });
  await stream.getByRole("button", { name: "Try again" }).click();
  await expect(
    stream.getByRole("link", { name: "e2e payment gateway" }).first(),
  ).toBeVisible();
  await expect(stream.getByText("Couldn't load changes")).toHaveCount(0);
  // Only the deliberately aborted change calls may have failed; everything
  // else on the page must have loaded quietly.
  expect(
    issues.failedRequests.every((call) => call.includes("/api/v1/changes")),
  ).toBe(true);
  expect(issues.pageErrors).toEqual([]);
  expect(issues.consoleErrors).toEqual([]);
});

test("empty stream, forbidden, and missing objects stay safe", async ({ page, request }) => {
  const issues = trackBrowserIssues(page);
  await loginViaApi(page, request);

  await page.goto("/app/intelligence");
  await expect(page.getByRole("heading", { name: "Intelligence" })).toBeVisible();
  await selectOrganisation(page, "E2E Alpha Org");
  await page.goto("/app/intelligence?change_type=RISK_DEESCALATED");
  await expect(page.getByText("No changes match these filters")).toBeVisible();

  await page.goto("/app/meetings/does-not-exist");
  await expect(page.getByText("This meeting isn't available")).toBeVisible();
  await expect(page.getByRole("link", { name: /back to meetings/i })).toBeVisible();
  expectNoSevereBrowserIssues(issues);
});
