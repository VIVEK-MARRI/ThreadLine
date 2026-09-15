import { expect, test } from "@playwright/test";
import {
  expectNoSevereBrowserIssues,
  loginViaApi,
  selectOrganisation,
  trackBrowserIssues,
} from "./helpers";

/* URL and history behavior in a real browser: filters survive reload and
 * back/forward navigation, invalid values normalise, and organisation
 * switches reset tenant-scoped query state.
 */

test("meeting and entity filters survive reload, back, and forward", async ({
  page,
  request,
}) => {
  const issues = trackBrowserIssues(page);
  await loginViaApi(page, request);

  await page.goto("/app/meetings");
  await expect(page.getByRole("heading", { name: "Meetings", exact: true })).toBeVisible();
  await selectOrganisation(page, "E2E Alpha Org");

  await page.getByLabel("Search loaded meetings").fill("Alpha");
  await expect(page).toHaveURL(/q=Alpha/);
  await page.getByLabel("Sort").selectOption("oldest");
  await expect(page).toHaveURL(/sort=oldest/);

  await page.reload();
  await expect(page.getByLabel("Search loaded meetings")).toHaveValue("Alpha");
  await expect(page.getByLabel("Sort")).toHaveValue("oldest");

  await page.getByRole("link", { name: /E2E Alpha Planning Sync/ }).first().click();
  await expect(
    page.getByRole("heading", { name: /E2E Alpha Planning Sync/ }),
  ).toBeVisible();
  await page.goBack();
  await expect(page.getByLabel("Search loaded meetings")).toHaveValue("Alpha");
  await page.goForward();
  await expect(
    page.getByRole("heading", { name: "E2E Alpha Planning Sync" }),
  ).toBeVisible();

  await page.goto("/app/entities");
  await expect(page.getByRole("heading", { name: "Entities" })).toBeVisible();
  await page.getByLabel("Search entities").fill("gateway");
  await expect(page).toHaveURL(/q=gateway/);
  await page.reload();
  await expect(page.getByLabel("Search entities")).toHaveValue("gateway");
  expectNoSevereBrowserIssues(issues);
});

test("intelligence filters persist, normalise, and reset on organisation switch", async ({
  page,
  request,
}) => {
  const issues = trackBrowserIssues(page);
  await loginViaApi(page, request);

  await page.goto("/app/intelligence?severity=BOGUS&change_type=STATE_BLOCKED");
  await expect(page.getByRole("heading", { name: "Intelligence" })).toBeVisible();
  await expect(page.getByLabel("Severity")).toHaveValue("ALL");
  await expect(page.getByLabel("Change type")).toHaveValue("STATE_BLOCKED");
  await selectOrganisation(page, "E2E Alpha Org");

  await page.getByLabel("Severity").selectOption("HIGH");
  await expect(page).toHaveURL(/severity=HIGH/);
  await page.reload();
  await expect(page.getByLabel("Severity")).toHaveValue("HIGH");

  await page.getByRole("button", { name: "Switch organisation" }).click();
  await page.getByRole("menuitem", { name: "E2E Beta Org" }).click();
  await expect(page).not.toHaveURL(/severity=HIGH/);
  await expect(page.getByLabel("Severity")).toHaveValue("ALL");
  expectNoSevereBrowserIssues(issues);
});
