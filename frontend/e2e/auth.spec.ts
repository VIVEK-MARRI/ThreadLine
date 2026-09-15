import { expect, test } from "@playwright/test";
import {
  NOMEMBER_EMAIL,
  OWNER_EMAIL,
  PASSWORD,
  expectNoSevereBrowserIssues,
  loginViaApi,
  trackBrowserIssues,
} from "./helpers";

/* Real browser auth lifecycle against the real backend: guards, login with
 * return-to, logout, invalid sessions, and the no-organisation recovery.
 */

test("unauthenticated visit to a protected route lands on login", async ({ page }) => {
  await page.goto("/app/intelligence");
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
});

test("login preserves the return-to route", async ({ page }) => {
  const issues = trackBrowserIssues(page);
  await page.goto("/app/intelligence");
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  await page.getByLabel("Email").fill(OWNER_EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Intelligence" })).toBeVisible();
  expectNoSevereBrowserIssues(issues);
});

test("logout clears the session and protects routes again", async ({ page, request }) => {
  const issues = trackBrowserIssues(page);
  await loginViaApi(page, request);
  await page.goto("/app/dashboard");
  await expect(
    page.getByRole("heading", { name: /good (morning|afternoon|evening)/i }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Account" }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("tl.session.token"))).toBeNull();
  expectNoSevereBrowserIssues(issues);
});

test("invalid stored session returns to login", async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.setItem("tl.session.token", "bogus-token");
  });
  await page.goto("/app/dashboard");
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("tl.session.token"))).toBeNull();
});

test("memberless account sees safe recovery, not a crash", async ({ page, request }) => {
  const issues = trackBrowserIssues(page);
  await loginViaApi(page, request, NOMEMBER_EMAIL, PASSWORD);
  await page.goto("/app/dashboard");
  await expect(page.getByText("No organisation yet")).toBeVisible();
  await page.getByRole("link", { name: /go to settings/i }).click();
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  expectNoSevereBrowserIssues(issues);
});
