/* Landing page E2E — Playwright browser validation.
 * Validates at 1440×900, 1024×768, 390×844. */

import { test, expect, type Page } from "@playwright/test";
import { AxeBuilder } from "@axe-core/playwright";
import {
  trackBrowserIssues,
  expectNoSevereBrowserIssues,
  expectNoHorizontalOverflow,
} from "./helpers";

const VIEWPORTS = [
  { width: 1440, height: 900, label: "desktop-1440" },
  { width: 1024, height: 768, label: "tablet-1024" },
  { width: 390, height: 844, label: "mobile-390" },
] as const;

test.describe("Landing page", () => {
  for (const vp of VIEWPORTS) {
    test.describe(`at ${vp.label}`, () => {
      test.use({ viewport: { width: vp.width, height: vp.height } });

      test("page loads and hero renders", async ({ page }) => {
        const issues = trackBrowserIssues(page);
        await page.goto("/");
        await expect(
          page.getByRole("heading", { level: 1 }),
        ).toContainText("Your organisation remembers what happened.");
        expectNoSevereBrowserIssues(issues);
      });

      test("no horizontal overflow", async ({ page }) => {
        await page.goto("/");
        await page.waitForLoadState("networkidle");
        await expectNoHorizontalOverflow(page);
      });

      test("axe accessibility passes", async ({ page }) => {
        await page.goto("/");
        await page.waitForLoadState("networkidle");
        const results = await new AxeBuilder({ page }).analyze();
        const violations = results.violations.map(
          (v) =>
            `${v.id} (${v.nodes.length}): ${v.help} :: ${v.nodes
              .slice(0, 5)
              .map((n) => n.target.join(" "))
              .join(" || ")}`,
        );
        expect(violations).toEqual([]);
      });
    });
  }

  test("CTA navigates to auth flow", async ({ page }) => {
    await page.goto("/");
    const cta = page.getByRole("link", { name: "Open ThreadLine" }).first();
    await cta.click();
    // Should end up at /login (unauthenticated) or /app
    await page.waitForURL(/\/(login|app)/);
    expect(page.url()).toMatch(/\/(login|app)/);
  });

  test("hero SVG animation elements are visible", async ({ page }) => {
    await page.goto("/");
    await page.waitForTimeout(2000); // Allow animation time
    const dots = page.locator(".tl-thread-dot");
    const count = await dots.count();
    expect(count).toBeGreaterThan(0);
  });

  test.describe("mobile menu at 390×844", () => {
    test.use({ viewport: { width: 390, height: 844 } });

    test("mobile menu opens and closes", async ({ page }) => {
      await page.goto("/");
      const menuBtn = page.getByRole("button", { name: "Menu" });
      await menuBtn.click();
      await expect(
        page.getByRole("navigation", { name: /Mobile navigation/i }),
      ).toBeVisible();

      // Close with the close button
      const closeBtn = page.getByRole("button", { name: "Close menu" });
      await closeBtn.click();
      await expect(
        page.getByRole("navigation", { name: /Mobile navigation/i }),
      ).not.toBeVisible();
    });
  });

  test("reduced-motion removes animation", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/");
    // Sections should be immediately visible (no transition delay)
    const hero = page.locator(".tl-landing-hero");
    await expect(hero).toBeVisible();
  });

  test("no console errors on landing page", async ({ page }) => {
    const issues = trackBrowserIssues(page);
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    expectNoSevereBrowserIssues(issues);
  });

  test("navigation section links scroll to targets", async ({ page }) => {
    await page.goto("/");
    // Click "How it works" link
    await page.getByRole("link", { name: "How it works" }).first().click();
    await page.waitForTimeout(500);
    // The section should be scrolled into view
    const section = page.locator("#how-it-works");
    await expect(section).toBeVisible();
  });

  test("evidence hover highlights the traced thread", async ({ page }) => {
    await page.goto("/");
    const signal = page.getByLabel("Intelligence signal: Blocked dependency detected");
    await signal.scrollIntoViewIfNeeded();
    await signal.hover();
    await expect(page.locator(".tl-evidence-node-traced").first()).toBeVisible();
  });

  test("ask illustration reveals answer and cited evidence", async ({ page }) => {
    await page.goto("/");
    await page.getByText("What happened to the payment gateway?").scrollIntoViewIfNeeded();
    await expect(
      page.getByText("The payment gateway is currently blocked."),
    ).toBeVisible();
    await expect(page.getByText("Cited evidence")).toBeVisible();
  });

  test("follow-the-thread stages activate on scroll", async ({ page }) => {
    await page.goto("/");
    const thread = page.getByRole("list", { name: "Follow the thread" });
    await thread.scrollIntoViewIfNeeded();
    await page.waitForTimeout(800);
    const active = await page.locator(".tl-follow-stage-active").count();
    expect(active).toBeGreaterThan(0);
  });

  test("screenshot: hero desktop", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await page.waitForTimeout(2500); // Allow hero animation
    await page.screenshot({
      path: "e2e/screenshots/landing-hero-desktop.png",
      fullPage: false,
    });
  });

  test("screenshot: meeting to memory", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await page.locator("#how-it-works").scrollIntoViewIfNeeded();
    await page.waitForTimeout(1500);
    await page.screenshot({
      path: "e2e/screenshots/landing-meeting-memory.png",
      fullPage: false,
    });
  });

  test("screenshot: organisation intelligence", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await page.locator("#intelligence").scrollIntoViewIfNeeded();
    await page.waitForTimeout(1500);
    await page.screenshot({
      path: "e2e/screenshots/landing-intelligence.png",
      fullPage: false,
    });
  });

  test("screenshot: evidence chain", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await page.locator("#evidence").scrollIntoViewIfNeeded();
    await page.waitForTimeout(1500);
    await page.screenshot({
      path: "e2e/screenshots/landing-evidence.png",
      fullPage: false,
    });
  });

  test("screenshot: follow the thread", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await page.locator("#thread").scrollIntoViewIfNeeded();
    await page.waitForTimeout(1500);
    await page.screenshot({
      path: "e2e/screenshots/landing-follow-thread.png",
      fullPage: false,
    });
  });

  test("screenshot: mobile view", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await page.waitForTimeout(1000);
    await page.screenshot({
      path: "e2e/screenshots/landing-mobile.png",
      fullPage: false,
    });
  });

  test("screenshot: full page desktop", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await page.waitForTimeout(3000);
    // Scroll to trigger all sections
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForTimeout(1500);
    await page.screenshot({
      path: "e2e/screenshots/landing-full-desktop.png",
      fullPage: true,
    });
  });
});
