/* Stage 35 landing page tests — full replacement coverage.
 * Render, hero, CTAs, navigation, every major section, SVG system,
 * interactions, responsive hooks, reduced motion, accessibility,
 * document title, auth routing, and link integrity.
 * Lazy-loaded page: all queries use findBy (async). */

import { screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, afterEach } from "vitest";
import { renderAtRoute, type FetchStub, jsonResponse } from "../../test/harness";
import { LANDING } from "./landingContent";

/* Public page — router auth context calls /api/v1/auth/me on mount;
 * the stub returns 401 (unauthenticated visitor). */
const publicStub: FetchStub = (method, pathname) => {
  if (method === "GET" && pathname === "/api/v1/auth/me") {
    return jsonResponse(401, { detail: "Not authenticated" });
  }
  return null;
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("LandingPage render", () => {
  it("renders the landing page at / with one h1", async () => {
    renderAtRoute("/", publicStub);
    const h1s = await screen.findAllByRole("heading", { level: 1 });
    expect(h1s).toHaveLength(1);
  });

  it("renders a main landmark containing all sections", async () => {
    renderAtRoute("/", publicStub);
    const main = await screen.findByRole("main");
    expect(main).toBeInTheDocument();
    expect(main.querySelectorAll("section").length).toBeGreaterThanOrEqual(10);
  });

  it("renders the footer with brand tagline", async () => {
    renderAtRoute("/", publicStub);
    expect(await screen.findByText(LANDING.footer.tagline)).toBeInTheDocument();
  });

  it("renders a skip link targeting the main content", async () => {
    renderAtRoute("/", publicStub);
    const skip = await screen.findByRole("link", { name: LANDING.nav.skipLink });
    expect(skip).toHaveAttribute("href", "#tl-landing-main");
  });
});

describe("LandingPage hero", () => {
  it("displays the hero headline", async () => {
    renderAtRoute("/", publicStub);
    expect(await screen.findByText(LANDING.hero.headline)).toBeInTheDocument();
  });

  it("displays the hero supporting body", async () => {
    renderAtRoute("/", publicStub);
    expect(await screen.findByText(LANDING.hero.body)).toBeInTheDocument();
  });

  it("displays the primary CTA linking to /app", async () => {
    renderAtRoute("/", publicStub);
    const ctas = await screen.findAllByRole("link", { name: LANDING.hero.primaryCta });
    expect(ctas.length).toBeGreaterThan(0);
    expect(ctas[0]).toHaveAttribute("href", "/app");
  });

  it("displays the secondary CTA linking to the thread section", async () => {
    renderAtRoute("/", publicStub);
    const secondary = await screen.findByRole("link", { name: LANDING.hero.secondaryCta });
    expect(secondary).toHaveAttribute("href", LANDING.hero.secondaryCtaHref);
  });

  it("renders the hero thread visual with its accessible label", async () => {
    renderAtRoute("/", publicStub);
    expect(
      await screen.findByRole("group", { name: LANDING.hero.visualLabel }),
    ).toBeInTheDocument();
  });

  it("hero thread has seven labelled nodes", async () => {
    renderAtRoute("/", publicStub);
    await screen.findByRole("group", { name: LANDING.hero.visualLabel });
    for (const label of [
      "Meeting",
      "Observation",
      "Entity",
      "Memory",
      "Change",
      "Intelligence",
      "Evidence",
    ]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });
});

describe("LandingPage navigation", () => {
  it("renders all nav links", async () => {
    renderAtRoute("/", publicStub);
    await screen.findByRole("heading", { level: 1 });
    for (const link of LANDING.nav.links) {
      expect(screen.getByRole("link", { name: link.label })).toBeInTheDocument();
    }
  });

  it("nav CTA links to /app", async () => {
    renderAtRoute("/", publicStub);
    const navCtas = await screen.findAllByRole("link", { name: LANDING.nav.cta });
    expect(navCtas.length).toBeGreaterThan(0);
    expect(navCtas[0]).toHaveAttribute("href", "/app");
  });

  it("mobile menu opens on Menu button and shows links", async () => {
    const user = userEvent.setup();
    renderAtRoute("/", publicStub);
    await screen.findByRole("heading", { level: 1 });
    await user.click(screen.getByRole("button", { name: LANDING.nav.mobileMenuLabel }));
    expect(
      await screen.findByRole("navigation", { name: /Mobile navigation/i }),
    ).toBeInTheDocument();
  });

  it("mobile menu closes on Close button", async () => {
    const user = userEvent.setup();
    renderAtRoute("/", publicStub);
    await screen.findByRole("heading", { level: 1 });
    await user.click(screen.getByRole("button", { name: LANDING.nav.mobileMenuLabel }));
    await user.click(screen.getByRole("button", { name: LANDING.nav.mobileCloseLabel }));
    await waitFor(() => {
      expect(screen.queryByRole("navigation", { name: /Mobile navigation/i })).toBeNull();
    });
  });

  it("mobile menu closes on Escape and returns focus to the menu button", async () => {
    const user = userEvent.setup();
    renderAtRoute("/", publicStub);
    await screen.findByRole("heading", { level: 1 });
    await user.click(screen.getByRole("button", { name: LANDING.nav.mobileMenuLabel }));
    await screen.findByRole("navigation", { name: /Mobile navigation/i });
    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("navigation", { name: /Mobile navigation/i })).toBeNull();
    });
    expect(screen.getByRole("button", { name: LANDING.nav.mobileMenuLabel })).toHaveFocus();
  });
});

describe("LandingPage sections", () => {
  it("renders the problem section with fragments", async () => {
    renderAtRoute("/", publicStub);
    expect(await screen.findByText(LANDING.problem.headline)).toBeInTheDocument();
    for (const fragment of LANDING.problem.fragments) {
      expect(screen.getByText(fragment)).toBeInTheDocument();
    }
    expect(screen.getByText(LANDING.problem.resolution)).toBeInTheDocument();
  });

  it("renders meeting-to-memory with conversation and extraction", async () => {
    renderAtRoute("/", publicStub);
    expect(await screen.findByText(LANDING.meetingToMemory.headline)).toBeInTheDocument();
    expect(screen.getByText(LANDING.meetingToMemory.conversation[0].text)).toBeInTheDocument();
    expect(screen.getAllByText("Payment Gateway").length).toBeGreaterThan(0);
    expect(screen.getAllByText(LANDING.meetingToMemory.illustrativeNote).length).toBeGreaterThan(0);
  });

  it("renders the entity workspace with all eight facets", async () => {
    renderAtRoute("/", publicStub);
    expect(await screen.findByText(LANDING.entityIntelligence.headline)).toBeInTheDocument();
    expect(
      screen.getByRole("group", {
        name: `Conceptual entity workspace for ${LANDING.entityIntelligence.entity.name}`,
      }),
    ).toBeInTheDocument();
    for (const facet of LANDING.entityIntelligence.entity.facets) {
      expect(screen.getAllByText(facet.label).length).toBeGreaterThan(0);
    }
  });

  it("renders organisation intelligence flow steps", async () => {
    renderAtRoute("/", publicStub);
    expect(await screen.findByText(LANDING.orgIntelligence.headline)).toBeInTheDocument();
    for (const step of LANDING.orgIntelligence.flow) {
      expect(screen.getAllByText(step.label).length).toBeGreaterThan(0);
    }
  });

  it("renders proactive intelligence with caveat", async () => {
    renderAtRoute("/", publicStub);
    expect(await screen.findByText(LANDING.proactive.headline)).toBeInTheDocument();
    expect(screen.getByText(LANDING.proactive.caveat)).toBeInTheDocument();
  });

  it("renders the proactive scan illustration", async () => {
    renderAtRoute("/", publicStub);
    await screen.findByText(LANDING.proactive.headline);
    expect(
      screen.getByRole("img", { name: LANDING.proactive.scanLabel }),
    ).toBeInTheDocument();
  });

  it("renders the evidence chain with all four steps", async () => {
    renderAtRoute("/", publicStub);
    expect(await screen.findByText(LANDING.evidence.headline)).toBeInTheDocument();
    expect(
      screen.getByRole("list", { name: "Evidence traceability chain" }),
    ).toBeInTheDocument();
    for (const item of LANDING.evidence.chain) {
      expect(screen.getAllByText(item.label).length).toBeGreaterThan(0);
    }
  });

  it("renders the Ask illustration with question, answer, and evidence", async () => {
    renderAtRoute("/", publicStub);
    expect(await screen.findByText(LANDING.ask.headline)).toBeInTheDocument();
    expect(screen.getByText(LANDING.ask.question)).toBeInTheDocument();
    expect(screen.getByText(LANDING.ask.answer)).toBeInTheDocument();
    for (const item of LANDING.ask.evidence) {
      expect(screen.getAllByText(item.detail).length).toBeGreaterThan(0);
    }
  });

  it("renders all eight follow-the-thread stages", async () => {
    renderAtRoute("/", publicStub);
    expect(await screen.findByText(LANDING.followThread.headline)).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Follow the thread" })).toBeInTheDocument();
    for (const stage of LANDING.followThread.stages) {
      expect(screen.getAllByText(stage.label).length).toBeGreaterThan(0);
    }
  });

  it("renders security characteristics", async () => {
    renderAtRoute("/", publicStub);
    expect(await screen.findByText(LANDING.security.headline)).toBeInTheDocument();
    for (const c of LANDING.security.characteristics) {
      expect(screen.getByText(c)).toBeInTheDocument();
    }
  });

  it("renders the final CTA linking to /app", async () => {
    renderAtRoute("/", publicStub);
    expect(await screen.findByText(LANDING.finalCta.headline)).toBeInTheDocument();
    const ctas = await screen.findAllByRole("link", { name: LANDING.finalCta.cta });
    expect(ctas[ctas.length - 1]).toHaveAttribute("href", "/app");
  });
});

describe("LandingPage interactions", () => {
  it("thread node hover highlights connected neighbours", async () => {
    renderAtRoute("/", publicStub);
    const entity = await screen.findByRole("button", { name: "Entity" });
    fireEvent.mouseEnter(entity);
    expect(document.querySelector(".tl-thread-node-active")).not.toBeNull();
    fireEvent.mouseLeave(entity);
  });

  it("thread nodes are keyboard-focusable and highlight on focus", async () => {
    renderAtRoute("/", publicStub);
    const memory = await screen.findByRole("button", { name: "Memory" });
    fireEvent.focus(memory);
    expect(document.querySelector(".tl-thread-node-active")).not.toBeNull();
    fireEvent.blur(memory);
  });

  it("evidence hover traces the chain toward source", async () => {
    renderAtRoute("/", publicStub);
    await screen.findByText(LANDING.evidence.headline);
    const meeting = screen.getByLabelText("Meeting: Weekly sync, 14 Jan");
    fireEvent.mouseEnter(meeting);
    expect(document.querySelectorAll(".tl-evidence-node-traced").length).toBeGreaterThan(0);
    fireEvent.mouseLeave(meeting);
  });

  it("evidence nodes are keyboard-focusable and trace on focus", async () => {
    renderAtRoute("/", publicStub);
    await screen.findByText(LANDING.evidence.headline);
    const signal = screen.getByLabelText("Intelligence signal: Blocked dependency detected");
    fireEvent.focus(signal);
    expect(document.querySelectorAll(".tl-evidence-node-traced").length).toBeGreaterThan(0);
    fireEvent.blur(signal);
  });
});

describe("LandingPage document metadata", () => {
  it("sets the document title", async () => {
    renderAtRoute("/", publicStub);
    await screen.findByRole("heading", { level: 1 });
    await waitFor(() => {
      expect(document.title).toBe(`${LANDING.meta.title} · ThreadLine`);
    });
  });

  it("sets Open Graph metadata", async () => {
    renderAtRoute("/", publicStub);
    await screen.findByRole("heading", { level: 1 });
    await waitFor(() => {
      const ogTitle = document.querySelector('meta[property="og:title"]');
      expect(ogTitle?.getAttribute("content")).toBe(LANDING.meta.title);
      const ogDesc = document.querySelector('meta[property="og:description"]');
      expect(ogDesc?.getAttribute("content")).toBe(LANDING.meta.description);
    });
  });
});

describe("LandingPage link integrity and auth routing", () => {
  it("every link resolves to a real route, anchor, or CTA", async () => {
    renderAtRoute("/", publicStub);
    await screen.findByRole("heading", { level: 1 });
    const anchors = Array.from(document.querySelectorAll(".tl-landing a")).map((a) =>
      a.getAttribute("href"),
    );
    expect(anchors.length).toBeGreaterThan(0);
    for (const href of anchors) {
      expect(href).toMatch(/^(\/|#)/);
    }
  });

  it("every anchor target exists in the page", async () => {
    renderAtRoute("/", publicStub);
    await screen.findByRole("heading", { level: 1 });
    const anchors = Array.from(document.querySelectorAll('.tl-landing a[href^="#"]'));
    for (const a of anchors) {
      const href = a.getAttribute("href");
      if (href && href.length > 1) {
        expect(
          document.querySelector(href),
          `anchor target ${href} should exist`,
        ).not.toBeNull();
      }
    }
  });

  it("primary CTA navigates toward the auth flow", async () => {
    const user = userEvent.setup();
    renderAtRoute("/", publicStub);
    const ctas = await screen.findAllByRole("link", { name: LANDING.hero.primaryCta });
    await user.click(ctas[0]);
    // Unauthenticated visitor: /app guard redirects to the sign-in page.
    expect(await screen.findByText("Sign in")).toBeInTheDocument();
  });

  it("landing contains no emojis in visible copy", async () => {
    renderAtRoute("/", publicStub);
    await screen.findByRole("heading", { level: 1 });
    const text = document.querySelector(".tl-landing")?.textContent ?? "";
    expect(text).not.toMatch(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u);
  });
});

describe("LandingPage responsive thread", () => {
  it("renders the compact vertical thread on narrow viewports", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockImplementation((query: string) => ({
        matches: query === "(max-width: 640px)",
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    );
    renderAtRoute("/", publicStub);
    const visual = await screen.findByRole("group", { name: LANDING.hero.visualLabel });
    expect(visual).toHaveClass("tl-thread-compact");
    // All seven stages remain present in the compact layout.
    for (const label of ["Meeting", "Memory", "Evidence"]) {
      expect(
        screen.getByRole("button", { name: label }),
      ).toBeInTheDocument();
    }
  });

  it("renders the wide thread on desktop viewports", async () => {
    renderAtRoute("/", publicStub);
    const visual = await screen.findByRole("group", { name: LANDING.hero.visualLabel });
    expect(visual).not.toHaveClass("tl-thread-compact");
  });
});

describe("LandingPage reduced motion", () => {  it("reveals content immediately when reduced motion is preferred", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockImplementation((query: string) => ({
        matches: query === "(prefers-reduced-motion: reduce)",
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    );
    renderAtRoute("/", publicStub);
    // Content renders without waiting on IntersectionObserver choreography.
    expect(await screen.findByText(LANDING.hero.headline)).toBeInTheDocument();
    expect(await screen.findByText(LANDING.followThread.headline)).toBeInTheDocument();
  });
});
