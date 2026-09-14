import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { jsonResponse, renderAtRoute } from "../../test/harness";

/* Dashboard integration tests: the page renders real backend shapes
 * (attention / portfolio / changes / job health / meeting detail) joined
 * for display, and every important row navigates to a real route.
 */

const EVALUATED = "2026-09-13T10:00:00Z";

function membership(orgId: string, name: string, role: string) {
  return {
    organisation: {
      organisation_id: orgId,
      name,
      slug: name.toLowerCase(),
      status: "ACTIVE",
      created_at: "2026-01-01T00:00:00Z",
    },
    role,
    status: "ACTIVE",
  };
}

function mePayload(email: string, memberships: ReturnType<typeof membership>[]) {
  return {
    user: { user_id: "u-9", email, status: "ACTIVE", created_at: "2026-01-01T00:00:00Z" },
    memberships,
  };
}

const ATTENTION = {
  entity_count: 2,
  items: [
    {
      attention_id: "att-1",
      entity_id: "ent-pay",
      attention_level: "CRITICAL",
      score: 120,
      reasons: ["ENTITY_BLOCKED"],
      related_insight_ids: ["ins-1"],
      evaluated_at: EVALUATED,
    },
    {
      attention_id: "att-2",
      entity_id: "ent-api",
      attention_level: "HIGH",
      score: 60,
      reasons: ["RECENT_STATE_CHANGE", "REPEATED_OBSERVATION"],
      related_insight_ids: [],
      evaluated_at: EVALUATED,
    },
  ],
};

const PORTFOLIO = {
  total_entities: 3,
  critical_entities: 1,
  high_risk_entities: 1,
  medium_risk_entities: 0,
  low_risk_entities: 1,
  entities_with_active_actions: 1,
  entities_with_impact: 1,
  blocked_entities: 1,
  entities: [
    {
      entity_id: "ent-pay",
      entity_type: "ISSUE",
      canonical_name: "Payment migration",
      risk_level: "CRITICAL",
      attention_level: "CRITICAL",
      attention_score: 120,
      impact_count: 2,
      action_count: 1,
      active_insight_count: 3,
      current_state: "BLOCKED",
      observation_count: 5,
    },
    {
      entity_id: "ent-api",
      entity_type: "ISSUE",
      canonical_name: "Provider API flakiness",
      risk_level: "HIGH",
      attention_level: "HIGH",
      attention_score: 60,
      impact_count: 1,
      action_count: 0,
      active_insight_count: 2,
      current_state: "IN_PROGRESS",
      observation_count: 4,
    },
    {
      entity_id: "ent-docs",
      entity_type: "TASK",
      canonical_name: "Docs refresh",
      risk_level: "LOW",
      attention_level: null,
      attention_score: 0,
      impact_count: 0,
      action_count: 0,
      active_insight_count: 0,
      current_state: "OPEN",
      observation_count: 1,
    },
  ],
  evaluated_at: EVALUATED,
};

function change(overrides: Record<string, unknown> = {}) {
  return {
    change_id: "chg-1",
    entity_id: "ent-pay",
    change_type: "RISK_ESCALATED",
    severity: "CRITICAL",
    detected_at: "2026-09-10T10:00:00Z",
    meeting_id: "mtg-1",
    mention_id: null,
    source_text: null,
    previous_state: null,
    current_state: "BLOCKED",
    insight_id: "ins-1",
    dependency_path: null,
    impact_count: null,
    related_entity_ids: ["ent-api"],
    evidence: "Payment migration entered BLOCKED state with CRITICAL attention.",
    ...overrides,
  };
}

const CHANGES = {
  total_changes: 2,
  critical_changes: 1,
  high_changes: 1,
  medium_changes: 0,
  info_changes: 0,
  changes: [
    change(),
    change({
      change_id: "chg-2",
      entity_id: "ent-api",
      change_type: "STATE_BLOCKED",
      severity: "HIGH",
      detected_at: null,
      meeting_id: null,
      insight_id: null,
      related_entity_ids: [],
      evidence: "Provider API flakiness transitioned to BLOCKED.",
    }),
  ],
  evaluated_at: EVALUATED,
};

const JOBS = {
  worker_enabled: true,
  counts: { PENDING: 2, RUNNING: 1, RETRY_WAITING: 0, FAILED: 0, CANCELLED: 0 },
  stale_running: 0,
  oldest_pending_age_seconds: 300,
};

const MEETING = {
  meeting_id: "mtg-1",
  title: "Payment sync",
  transcript: "…",
  meeting_date: "2026-09-10T09:00:00Z",
  participants: [],
  ingested_at: "2026-09-10T09:30:00Z",
};

function dashboardStub(overrides: Record<string, (init: RequestInit) => Response | null> = {}) {
  return (_method: string, pathname: string, init: RequestInit) => {
    if (overrides[pathname]) return overrides[pathname](init);
    if (pathname === "/api/v1/auth/me") {
      return jsonResponse(200, mePayload("sam@example.com", [membership("org-a", "Alpha", "OWNER")]));
    }
    if (pathname === "/api/v1/attention") return jsonResponse(200, ATTENTION);
    if (pathname === "/api/v1/portfolio") return jsonResponse(200, PORTFOLIO);
    if (pathname === "/api/v1/changes") return jsonResponse(200, CHANGES);
    if (pathname === "/api/v1/health/jobs") return jsonResponse(200, JOBS);
    if (pathname === "/api/v1/meetings/mtg-1") return jsonResponse(200, MEETING);
    return null;
  };
}

const WAIT = { timeout: 15000 } as const;

describe("dashboard", () => {
  it("renders authenticated organisation data with real navigation", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/dashboard", dashboardStub());

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /good (morning|afternoon|evening), sam/i }),
      ).toBeInTheDocument();
    }, WAIT);
    // Attention content proves the tenant queries resolved; only then
    // assert the aggregate snapshot line.
    const attentionSection = () =>
      within(
        screen.getByRole("heading", { name: /needs attention/i }).closest("section") as HTMLElement,
      );
    await waitFor(() => {
      expect(attentionSection().getByText("Payment migration")).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByText(/organisation overview for alpha/i)).toBeInTheDocument();
    // Snapshot line uses real aggregate counts, not stat cards.
    expect(screen.getByText(/3 entities · 2 need attention · 1 blocked/i)).toBeInTheDocument();

    // Attention: backend ordering + reasons, joined to portfolio names.
    expect(attentionSection().getByText("Blocked")).toBeInTheDocument();
    expect(
      attentionSection().getByRole("link", { name: "Payment migration" }),
    ).toHaveAttribute("href", "/app/entities/ent-pay");

    // Changes: type label, evidence, meeting navigation with resolved title.
    await waitFor(() => {
      expect(screen.getByText(/entered BLOCKED state with CRITICAL attention/i)).toBeInTheDocument();
    }, WAIT);
    expect(
      screen.getByRole("link", { name: /discussed in payment sync/i }),
    ).toHaveAttribute("href", "/app/meetings/mtg-1");

    // Processing: human-readable queue state from real job counts.
    expect(screen.getByText(/2 queued · 1 processing · longest wait 5m/i)).toBeInTheDocument();

    // Risks: blocked entity with impact/action framing, not an attention copy.
    const risks = screen.getByRole("heading", { name: /risks & blockers/i }).closest("section");
    expect(within(risks as HTMLElement).getByText("Payment migration")).toBeInTheDocument();
    expect(
      within(risks as HTMLElement).getByText(/blocked · 2 impact associations · 1 recommended action/i),
    ).toBeInTheDocument();

    // Primary action routes to the real meetings workspace.
    expect(screen.getByRole("link", { name: /ingest a meeting/i })).toHaveAttribute(
      "href",
      "/app/meetings",
    );
  });

  it("shows loading skeletons while sections resolve", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute(
      "/app/dashboard",
      dashboardStub({
        "/api/v1/attention": () => new Promise<Response>(() => {}) as unknown as Response,
      }),
    );

    await waitFor(() => {
      expect(screen.getByText(/entered BLOCKED state/i)).toBeInTheDocument();
    }, WAIT);
    // Attention still pending: structural skeleton, not an empty page.
    expect(
      screen.getAllByRole("status", { name: /loading content/i }).length,
    ).toBeGreaterThan(0);
  });

  it("shows calm empty states for a new organisation", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    const emptyPortfolio = { ...PORTFOLIO, total_entities: 0, entities: [] };
    renderAtRoute(
      "/app/dashboard",
      dashboardStub({
        "/api/v1/attention": () => jsonResponse(200, { entity_count: 0, items: [] }),
        "/api/v1/portfolio": () => jsonResponse(200, emptyPortfolio),
        "/api/v1/changes": () =>
          jsonResponse(200, {
            total_changes: 0,
            critical_changes: 0,
            high_changes: 0,
            medium_changes: 0,
            info_changes: 0,
            changes: [],
            evaluated_at: EVALUATED,
          }),
        "/api/v1/health/jobs": () =>
          jsonResponse(200, {
            worker_enabled: true,
            counts: { PENDING: 0, RUNNING: 0, RETRY_WAITING: 0, FAILED: 0, CANCELLED: 0 },
            stale_running: 0,
            oldest_pending_age_seconds: null,
          }),
      }),
    );

    await waitFor(() => {
      expect(screen.getByText("Nothing needs attention")).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByText("No significant organisational changes yet")).toBeInTheDocument();
    expect(screen.getByText("No meetings in the queue.")).toBeInTheDocument();
    expect(screen.getByText("No elevated risks")).toBeInTheDocument();
    expect(screen.queryByText(/no data found/i)).not.toBeInTheDocument();
  });

  it("keeps working sections when one API fails", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute(
      "/app/dashboard",
      dashboardStub({
        "/api/v1/attention": () => jsonResponse(500, { detail: "boom" }),
      }),
    );

    await waitFor(() => {
      expect(screen.getByText("Couldn't load attention")).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
    // Sibling sections still render from their own successful queries.
    expect(screen.getByText(/entered BLOCKED state/i)).toBeInTheDocument();
    expect(screen.getByText(/2 queued · 1 processing/i)).toBeInTheDocument();
  });

  it("surfaces failed and stale queue states honestly", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute(
      "/app/dashboard",
      dashboardStub({
        "/api/v1/health/jobs": () =>
          jsonResponse(200, {
            worker_enabled: true,
            counts: { PENDING: 0, RUNNING: 0, RETRY_WAITING: 0, FAILED: 2, CANCELLED: 0 },
            stale_running: 1,
            oldest_pending_age_seconds: null,
          }),
      }),
    );

    await waitFor(() => {
      expect(screen.getByText("No meetings in the queue.")).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByText(/2 failed · 1 stale/i)).toBeInTheDocument();
  });

  it("says so when the background worker is off", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute(
      "/app/dashboard",
      dashboardStub({
        "/api/v1/health/jobs": () =>
          jsonResponse(200, {
            worker_enabled: false,
            counts: { PENDING: 1, RUNNING: 0, RETRY_WAITING: 0, FAILED: 0, CANCELLED: 0 },
            stale_running: 0,
            oldest_pending_age_seconds: 60,
          }),
      }),
    );

    await waitFor(() => {
      expect(screen.getByText(/background worker is off/i)).toBeInTheDocument();
    }, WAIT);
  });

  it("shows the ingest action to members (backend grants meeting create)", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute(
      "/app/dashboard",
      (_method, pathname) => {
        if (pathname === "/api/v1/auth/me") {
          return jsonResponse(
            200,
            mePayload("sam@example.com", [membership("org-a", "Alpha", "MEMBER")]),
          );
        }
        if (pathname === "/api/v1/attention") return jsonResponse(200, ATTENTION);
        if (pathname === "/api/v1/portfolio") return jsonResponse(200, PORTFOLIO);
        if (pathname === "/api/v1/changes") return jsonResponse(200, CHANGES);
        if (pathname === "/api/v1/health/jobs") return jsonResponse(200, JOBS);
        if (pathname === "/api/v1/meetings/mtg-1") return jsonResponse(200, MEETING);
        return null;
      },
    );

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /good (morning|afternoon|evening), sam/i }),
      ).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByRole("link", { name: /ingest a meeting/i })).toHaveAttribute(
      "href",
      "/app/meetings",
    );
  });

  it("switches dashboard data with the organisation (tenant A ≠ tenant B)", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    const twoOrgs = mePayload("sam@example.com", [
      membership("org-a", "Alpha", "OWNER"),
      membership("org-b", "Beta", "MEMBER"),
    ]);
    const betaAttention = { entity_count: 0, items: [] };
    const betaPortfolio = {
      ...PORTFOLIO,
      total_entities: 1,
      critical_entities: 0,
      high_risk_entities: 0,
      medium_risk_entities: 0,
      low_risk_entities: 1,
      entities_with_active_actions: 0,
      entities_with_impact: 0,
      blocked_entities: 0,
      entities: [
        {
          entity_id: "ent-gamma",
          entity_type: "ISSUE",
          canonical_name: "Gamma rollout",
          risk_level: "LOW",
          attention_level: null,
          attention_score: 0,
          impact_count: 0,
          action_count: 0,
          active_insight_count: 1,
          current_state: "OPEN",
          observation_count: 2,
        },
      ],
    };
    renderAtRoute("/app/dashboard", (_method, pathname, init) => {
      const headers = new Headers(init.headers);
      const org = headers.get("X-Organisation-ID");
      if (pathname === "/api/v1/auth/me") return jsonResponse(200, twoOrgs);
      if (org === "org-b") {
        if (pathname === "/api/v1/attention") return jsonResponse(200, betaAttention);
        if (pathname === "/api/v1/portfolio") return jsonResponse(200, betaPortfolio);
        if (pathname === "/api/v1/changes") {
          return jsonResponse(200, {
            total_changes: 0,
            critical_changes: 0,
            high_changes: 0,
            medium_changes: 0,
            info_changes: 0,
            changes: [],
            evaluated_at: EVALUATED,
          });
        }
        if (pathname === "/api/v1/health/jobs") {
          return jsonResponse(200, {
            worker_enabled: true,
            counts: { PENDING: 0, RUNNING: 0, RETRY_WAITING: 0, FAILED: 0, CANCELLED: 0 },
            stale_running: 0,
            oldest_pending_age_seconds: null,
          });
        }
        return null;
      }
      if (pathname === "/api/v1/attention") return jsonResponse(200, ATTENTION);
      if (pathname === "/api/v1/portfolio") return jsonResponse(200, PORTFOLIO);
      if (pathname === "/api/v1/changes") return jsonResponse(200, CHANGES);
      if (pathname === "/api/v1/health/jobs") return jsonResponse(200, JOBS);
      if (pathname === "/api/v1/meetings/mtg-1") return jsonResponse(200, MEETING);
      return null;
    });

    await waitFor(() => {
      expect(
        within(screen.getByRole("heading", { name: /needs attention/i }).closest("section") as HTMLElement).getByText(
          "Payment migration",
        ),
      ).toBeInTheDocument();
    }, WAIT);

    await user.click(screen.getByRole("button", { name: /switch organisation/i }));
    await user.click(await screen.findByRole("menuitem", { name: /beta/i }));

    // Tenant A content is gone; only Tenant B data renders.
    await waitFor(() => {
      expect(screen.getByText("Nothing needs attention")).toBeInTheDocument();
    }, WAIT);
    expect(screen.queryByText("Payment migration")).not.toBeInTheDocument();
    expect(screen.getByText(/organisation overview for beta/i)).toBeInTheDocument();
  });
});
