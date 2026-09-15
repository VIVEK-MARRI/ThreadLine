import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { jsonResponse, renderAtRoute } from "../../test/harness";

/* Actions integration tests: portfolio-reported follow-up paths, honest empty
 * and error states, tenant switching, and navigation to entity detail.
 */

const WAIT = { timeout: 15000 } as const;
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
    user: { user_id: "u-13", email, status: "ACTIVE", created_at: "2026-01-01T00:00:00Z" },
    memberships,
  };
}

const PORTFOLIO = {
  total_entities: 2,
  critical_entities: 1,
  high_risk_entities: 0,
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
      entity_id: "ent-docs",
      entity_type: "ISSUE",
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

function actionsStub() {
  return (_method: string, pathname: string) => {
    if (pathname === "/api/v1/auth/me") {
      return jsonResponse(200, mePayload("sam@example.com", [membership("org-a", "Alpha", "MEMBER")]));
    }
    if (pathname === "/api/v1/portfolio") return jsonResponse(200, PORTFOLIO);
    if (pathname === "/api/v1/entities/ent-pay") {
      return jsonResponse(200, {
        entity_id: "ent-pay",
        entity_type: "ISSUE",
        canonical_name: "Payment migration",
        aliases: [],
        created_at: "2026-09-01T10:00:00Z",
      });
    }
    return null;
  };
}

describe("actions workspace", () => {
  it("shows a structural loading state", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    let resolvePortfolio!: (value: Response) => void;
    const pendingPortfolio = new Promise<Response>((resolve) => {
      resolvePortfolio = resolve;
    });
    const rendered = renderAtRoute("/app/actions", (_method, pathname) => {
      if (pathname === "/api/v1/auth/me") {
        return jsonResponse(200, mePayload("sam@example.com", [membership("org-a", "Alpha", "MEMBER")]));
      }
      if (pathname === "/api/v1/portfolio") return pendingPortfolio as unknown as Response;
      return null;
    });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /^actions$/i })).toBeInTheDocument();
    }, WAIT);
    expect(
      screen.getAllByRole("status", { name: /loading follow-up paths/i }).length,
    ).toBeGreaterThan(0);
    resolvePortfolio(jsonResponse(200, { ...PORTFOLIO, entities: [] }));
    await waitFor(() => {
      expect(screen.getByText("No follow-up paths")).toBeInTheDocument();
    }, WAIT);
    rendered.unmount();
  });

  it("lists entities with backend-reported follow-up work", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/actions", actionsStub());

    await waitFor(() => {
      expect(screen.getByRole("link", { name: "Payment migration" })).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByText(/1 recommended action/i)).toBeInTheDocument();
    expect(screen.queryByText("Docs refresh")).not.toBeInTheDocument();
  });

  it("explains an organisation with no follow-up work", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/actions", (_method, pathname) => {
      if (pathname === "/api/v1/auth/me") {
        return jsonResponse(200, mePayload("sam@example.com", [membership("org-a", "Alpha", "MEMBER")]));
      }
      if (pathname === "/api/v1/portfolio") {
        return jsonResponse(200, { ...PORTFOLIO, entities_with_active_actions: 0, entities: [] });
      }
      return null;
    });

    await waitFor(() => {
      expect(screen.getByText("No follow-up paths")).toBeInTheDocument();
    }, WAIT);
  });

  it("retries portfolio failures and preserves the entity route", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    let portfolioCalls = 0;
    renderAtRoute("/app/actions", (_method, pathname) => {
      if (pathname === "/api/v1/auth/me") {
        return jsonResponse(200, mePayload("sam@example.com", [membership("org-a", "Alpha", "MEMBER")]));
      }
      if (pathname === "/api/v1/portfolio") {
        portfolioCalls += 1;
        if (portfolioCalls < 3) return jsonResponse(500, { detail: "boom" });
        return jsonResponse(200, PORTFOLIO);
      }
      return null;
    });

    await waitFor(() => {
      expect(screen.getByText("Couldn't load follow-up paths")).toBeInTheDocument();
    }, WAIT);
    const before = vi
      .mocked(globalThis.fetch)
      .mock.calls.filter(([input]) => String(input).endsWith("/api/v1/portfolio")).length;
    await user.click(screen.getByRole("button", { name: /try again/i }));
    await waitFor(() => {
      const after = vi
        .mocked(globalThis.fetch)
        .mock.calls.filter(([input]) => String(input).endsWith("/api/v1/portfolio")).length;
      expect(after).toBeGreaterThan(before);
      expect(screen.getByRole("link", { name: "Payment migration" })).toBeInTheDocument();
    }, WAIT);
  });

  it("switches follow-up paths with the organisation", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    const twoOrgs = mePayload("sam@example.com", [
      membership("org-a", "Alpha", "MEMBER"),
      membership("org-b", "Beta", "MEMBER"),
    ]);
    renderAtRoute("/app/actions", (_method, pathname, init) => {
      if (pathname === "/api/v1/auth/me") return jsonResponse(200, twoOrgs);
      if (pathname === "/api/v1/portfolio") {
        const org = new Headers(init.headers).get("X-Organisation-ID");
        if (org === "org-b") {
          return jsonResponse(200, { ...PORTFOLIO, entities_with_active_actions: 0, entities: [] });
        }
        return jsonResponse(200, PORTFOLIO);
      }
      return null;
    });

    await waitFor(() => {
      expect(screen.getByRole("link", { name: "Payment migration" })).toBeInTheDocument();
    }, WAIT);
    await user.click(screen.getByRole("button", { name: /switch organisation/i }));
    await user.click(await screen.findByRole("menuitem", { name: /beta/i }));
    await waitFor(() => {
      expect(screen.getByText("No follow-up paths")).toBeInTheDocument();
    }, WAIT);
    expect(screen.queryByRole("link", { name: "Payment migration" })).not.toBeInTheDocument();
  });

  it("navigates from a follow-up path to entity detail", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/actions", actionsStub());

    await waitFor(() => {
      expect(screen.getByRole("link", { name: "Payment migration" })).toBeInTheDocument();
    }, WAIT);
    await user.click(screen.getByRole("link", { name: "Payment migration" }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Payment migration" })).toBeInTheDocument();
    }, WAIT);
  });
});
