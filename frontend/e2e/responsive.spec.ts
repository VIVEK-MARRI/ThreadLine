import { expect, test, type Page } from "@playwright/test";
import {
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  expectNoSevereBrowserIssues,
  loginViaApi,
  organisationId,
  resolveEntityId,
  selectOrganisation,
  trackBrowserIssues,
} from "./helpers";

/* Responsive validation at desktop, tablet, and mobile widths: every major
 * route renders its heading with no horizontal overflow, and the mobile
 * drawer remains a usable navigation structure.
 */

const VIEWPORTS = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "tablet", width: 1024, height: 768 },
  { name: "mobile", width: 390, height: 844 },
] as const;

async function checkRoute(page: Page, url: string, heading: string | RegExp): Promise<void> {
  await page.goto(url);
  await expect(page.getByRole("heading", { name: heading }).first()).toBeVisible();
  await expectNoHorizontalOverflow(page, url);
}

for (const viewport of VIEWPORTS) {
  test(`all major routes render without overflow at ${viewport.name} (${viewport.width}px)`, async ({
    page,
    request,
  }) => {
    const issues = trackBrowserIssues(page);
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const token = await loginViaApi(page, request);
    const orgA = await organisationId(request, token, "e2e-alpha");
    const gatewayId = await resolveEntityId(request, token, orgA, "e2e payment gateway");

    await page.goto("/app/dashboard");
    await expect(
      page.getByRole("heading", { name: /good (morning|afternoon|evening)/i }),
    ).toBeVisible();
    await selectOrganisation(page, "E2E Alpha Org");

    await checkRoute(page, "/app/dashboard", /good (morning|afternoon|evening)/i);
    await checkRoute(page, "/app/meetings", "Meetings");
    await checkRoute(page, "/app/meetings/e2e-alpha-launch", "E2E Alpha Launch Review");
    await checkRoute(page, "/app/entities", "Entities");
    await checkRoute(page, `/app/entities/${gatewayId}`, "e2e payment gateway");
    await checkRoute(page, "/app/intelligence", "Intelligence");
    await checkRoute(page, "/app/ask", "Ask ThreadLine");
    await checkRoute(page, "/app/actions", "Actions");
    await checkRoute(page, "/app/settings", "Settings");
    expectNoSevereBrowserIssues(issues);
  });
}

test("mobile drawer opens, navigates, and closes", async ({ page, request }) => {
  const issues = trackBrowserIssues(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await loginViaApi(page, request);
  await page.goto("/app/dashboard");
  await expect(
    page.getByRole("heading", { name: /good (morning|afternoon|evening)/i }),
  ).toBeVisible();
  await selectOrganisation(page, "E2E Alpha Org");

  await page.getByRole("button", { name: "Open navigation", expanded: false }).click();
  const drawer = page.getByRole("complementary", { name: "Mobile navigation" });
  await expect(drawer).toBeVisible();
  await drawer.getByRole("link", { name: "Meetings", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Meetings", exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Open navigation", expanded: false }),
  ).toBeVisible();
  await expectNoHorizontalOverflow(page);
  expectNoSevereBrowserIssues(issues);
});

test("representative screenshots", async ({ page, request }) => {
  const token = await loginViaApi(page, request);
  const orgA = await organisationId(request, token, "e2e-alpha");
  const gatewayId = await resolveEntityId(request, token, orgA, "e2e payment gateway");

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/app/dashboard");
  await expect(
    page.getByRole("heading", { name: /good (morning|afternoon|evening)/i }),
  ).toBeVisible();
  await selectOrganisation(page, "E2E Alpha Org");
  await page.screenshot({ path: "e2e/screenshots/dashboard-1440.png" });

  await page.goto(`/app/entities/${gatewayId}`);
  await expect(page.getByRole("heading", { name: "e2e payment gateway" })).toBeVisible();
  await page.screenshot({ path: "e2e/screenshots/entity-detail-1440.png", fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/app/ask");
  await expect(page.getByRole("heading", { name: "Ask ThreadLine" })).toBeVisible();
  await page.screenshot({ path: "e2e/screenshots/ask-mobile-390.png" });
});
