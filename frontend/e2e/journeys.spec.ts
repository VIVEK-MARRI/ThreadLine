import { expect, test, type Page } from "@playwright/test";
import {
  expectNoSevereBrowserIssues,
  loginViaApi,
  selectOrganisation,
  trackBrowserIssues,
} from "./helpers";

/* Real browser smoke journeys across the connected workspaces. Every step
 * asserts rendered state and destination routes against the seeded backend.
 */

function section(page: Page, heading: string) {
  return page.locator("section").filter({ has: page.getByRole("heading", { name: heading }) });
}

test("journey A: dashboard → intelligence → entity → meeting", async ({
  page,
  request,
}) => {
  const issues = trackBrowserIssues(page);
  await loginViaApi(page, request);
  await page.goto("/app/dashboard");
  await expect(
    page.getByRole("heading", { name: /good (morning|afternoon|evening)/i }),
  ).toBeVisible();
  await selectOrganisation(page, "E2E Alpha Org");

  await section(page, "Explore").getByRole("link", { name: "Intelligence" }).click();
  await expect(page.getByRole("heading", { name: "Intelligence" })).toBeVisible();

  await section(page, "Current attention")
    .getByRole("link", { name: "e2e payment gateway" })
    .first()
    .click();
  await expect(page.getByRole("heading", { name: "e2e payment gateway" })).toBeVisible();

  await section(page, "Related meetings")
    .getByRole("link", { name: "E2E Alpha Launch Review" })
    .click();
  await expect(page.getByRole("heading", { name: "E2E Alpha Launch Review" })).toBeVisible();
  expectNoSevereBrowserIssues(issues);
});

test("journey B: meetings → detail → entity → related entity", async ({
  page,
  request,
}) => {
  const issues = trackBrowserIssues(page);
  await loginViaApi(page, request);
  await page.goto("/app/meetings");
  await expect(page.getByRole("heading", { name: "Meetings", exact: true })).toBeVisible();
  await selectOrganisation(page, "E2E Alpha Org");

  await page.getByRole("link", { name: "E2E Alpha Launch Review" }).click();
  await expect(page.getByRole("heading", { name: "E2E Alpha Launch Review" })).toBeVisible();

  await section(page, "People and entities")
    .getByRole("link", { name: "e2e payment gateway" })
    .first()
    .click();
  await expect(page.getByRole("heading", { name: "e2e payment gateway" })).toBeVisible();

  await section(page, "Dependencies")
    .getByRole("link", { name: "e2e billing helper" })
    .first()
    .click();
  await expect(page.getByRole("heading", { name: "e2e billing helper" })).toBeVisible();
  expectNoSevereBrowserIssues(issues);
});

test("journey C: intelligence change → entity → meeting", async ({ page, request }) => {
  const issues = trackBrowserIssues(page);
  await loginViaApi(page, request);
  await page.goto("/app/intelligence");
  await expect(page.getByRole("heading", { name: "Intelligence" })).toBeVisible();
  await selectOrganisation(page, "E2E Alpha Org");

  await section(page, "Change stream")
    .getByRole("link", { name: "e2e payment gateway" })
    .first()
    .click();
  await expect(page.getByRole("heading", { name: "e2e payment gateway" })).toBeVisible();

  await section(page, "Related meetings")
    .getByRole("link", { name: "E2E Alpha Launch Review" })
    .click();
  await expect(page.getByRole("heading", { name: "E2E Alpha Launch Review" })).toBeVisible();
  expectNoSevereBrowserIssues(issues);
});

test("journey D: ask → answer → evidence → entity and meeting", async ({
  page,
  request,
}) => {
  const issues = trackBrowserIssues(page);
  await loginViaApi(page, request);
  await page.goto("/app/ask");
  await expect(page.getByRole("heading", { name: "Ask ThreadLine" })).toBeVisible();
  await selectOrganisation(page, "E2E Alpha Org");

  const question = "What happened to e2e payment gateway?";
  await page.getByLabel("Your question").fill(question);
  await page.getByRole("button", { name: "Ask ThreadLine" }).click();
  await expect(page.getByRole("heading", { name: "Answer" })).toBeVisible();
  const evidence = section(page, "Cited evidence");
  await expect(evidence.getByRole("link", { name: /Open linked entity/ }).first()).toBeVisible();

  await evidence.getByRole("link", { name: /Open linked entity/ }).first().click();
  await expect(page.getByRole("heading", { name: "e2e payment gateway" })).toBeVisible();

  // Navigating away clears the form (no stale answers); submit again and
  // follow the meeting link.
  await page.goto("/app/ask");
  await page.getByLabel("Your question").fill(question);
  await page.getByRole("button", { name: "Ask ThreadLine" }).click();
  await expect(page.getByRole("heading", { name: "Answer" })).toBeVisible();
  const evidenceAgain = section(page, "Cited evidence");
  await evidenceAgain.getByRole("link", { name: /Open source meeting/ }).first().click();
  await expect(page.getByRole("heading", { name: "E2E Alpha Launch Review" })).toBeVisible();
  expectNoSevereBrowserIssues(issues);
});

test("journey E: actions → entity → exact recommendations", async ({ page, request }) => {
  const issues = trackBrowserIssues(page);
  await loginViaApi(page, request);
  await page.goto("/app/actions");
  await expect(page.getByRole("heading", { name: "Actions" })).toBeVisible();
  await selectOrganisation(page, "E2E Alpha Org");

  await page.getByRole("link", { name: "e2e payment gateway" }).click();
  await expect(page.getByRole("heading", { name: "e2e payment gateway" })).toBeVisible();
  await expect(
    section(page, "Attention and recommended actions").getByText(/Escalate the blocker/),
  ).toBeVisible();
  expectNoSevereBrowserIssues(issues);
});

test("journey F: settings → organisation switch → changed content → logout", async ({
  page,
  request,
}) => {
  const issues = trackBrowserIssues(page);
  await loginViaApi(page, request);
  await page.goto("/app/settings");
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  await selectOrganisation(page, "E2E Alpha Org");

  await page.getByRole("button", { name: "Switch organisation" }).click();
  await page.getByRole("menuitem", { name: "E2E Beta Org" }).click();
  await expect(page.getByText("E2E Beta Org").first()).toBeVisible();

  await page.goto("/app/entities");
  await expect(page.getByRole("link", { name: "e2e checkout flow" })).toBeVisible();
  await expect(page.getByRole("link", { name: "e2e payment gateway" })).toHaveCount(0);

  await page.getByRole("button", { name: "Account" }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  expectNoSevereBrowserIssues(issues);
});
