import { expect, test } from "@playwright/test";
import {
  expectNoAxeViolations,
  expectNoSevereBrowserIssues,
  loginViaApi,
  selectOrganisation,
  trackBrowserIssues,
} from "./helpers";

/* Browser-level accessibility: automated axe scans plus real keyboard
 * interaction with the skip link, the mobile drawer, and evidence
 * disclosures. No ARIA was added to satisfy these checks.
 */

test("axe scans pass on dashboard, intelligence, and ask", async ({ page, request }) => {
  const issues = trackBrowserIssues(page);
  await loginViaApi(page, request);

  await page.goto("/app/dashboard");
  await expect(
    page.getByRole("heading", { name: /good (morning|afternoon|evening)/i }),
  ).toBeVisible();
  await selectOrganisation(page, "E2E Alpha Org");
  await expectNoAxeViolations(page);

  await page.goto("/app/intelligence");
  await expect(page.getByRole("heading", { name: "Intelligence" })).toBeVisible();
  await expectNoAxeViolations(page);

  await page.goto("/app/ask");
  await expect(page.getByRole("heading", { name: "Ask ThreadLine" })).toBeVisible();
  await expectNoAxeViolations(page);
  expectNoSevereBrowserIssues(issues);
});

test("keyboard users can skip, open and dismiss navigation, and expand evidence", async ({
  page,
  request,
}) => {
  const issues = trackBrowserIssues(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await loginViaApi(page, request);
  await page.goto("/app/dashboard");
  await expect(
    page.getByRole("heading", { name: /good (morning|afternoon|evening)/i }),
  ).toBeVisible();

  // Fresh load: the skip link is the first tab stop, and activating it
  // moves focus to the main region.
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#tl-main")).toBeFocused();

  await selectOrganisation(page, "E2E Alpha Org");

  const menuButton = page.getByRole("button", { name: "Open navigation", expanded: false });
  await menuButton.focus();
  await page.keyboard.press("Enter");
  const drawer = page.getByRole("complementary", { name: "Mobile navigation" });
  await expect(drawer).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "Open navigation", expanded: false }),
  ).toBeVisible();
  await expect(menuButton).toBeFocused();

  await page.goto("/app/intelligence");
  await expect(page.getByRole("heading", { name: "Intelligence" })).toBeVisible();
  const disclosure = page.locator("details").first();
  await disclosure.scrollIntoViewIfNeeded();
  const summary = disclosure.locator("summary").first();
  await summary.focus();
  await page.keyboard.press("Enter");
  await expect(disclosure).toHaveAttribute("open", "");
  expectNoSevereBrowserIssues(issues);
});
