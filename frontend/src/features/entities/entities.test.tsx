import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { jsonResponse, renderAtRoute } from "../../test/harness";

/* Entity directory integration tests: the canonical registry joined to the
 * organisation portfolio, transparent loaded-set controls, recording,
 * failure handling, and tenant switching.
 */

const WAIT = { timeout: 15000 } as const;

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

function entity(entityId: string, canonicalName: string, entityType = "ISSUE", aliases: string[] = []) {
  return {
    entity_id: entityId,
    entity_type: entityType,
    canonical_name: canonicalName,
    aliases,
    created_at: "2026-09-01T10:00:00Z",
  };
}

function portfolio(entities: Array<Record<string, unknown>>) {
  return {
    total_entities: entities.length,
    critical_entities: 0,
    high_risk_entities: 0,
    medium_risk_entities: 0,
    low_risk_entities: entities.length,
    entities_with_active_actions: 0,
    entities_with_impact: 0,
    blocked_entities: 0,
    entities,
    evaluated_at: "2026-09-15T10:00:00Z",
  };
}

function portfolioEntity(overrides: Record<string, unknown> = {}) {
  return {
    entity_id: "ent-pay",
    entity_type: "ISSUE",
    canonical_name: "Payment Gateway",
    risk_level: "HIGH",
    attention_level: "HIGH",
    attention_score: 58,
    impact_count: 1,
    action_count: 1,
    active_insight_count: 2,
    current_state: "BLOCKED",
    observation_count: 3,
    ...overrides,
  };
}

function authStub(role = "MEMBER") {
  return (_method: string, pathname: string) => {
    if (pathname === "/api/v1/auth/me") {
      return jsonResponse(200, mePayload("sam@example.com", [membership("org-a", "Alpha", role)]));
    }
    return null;
  };
}

describe("entity directory", () => {
  it("shows a structural loading state", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    let resolveEntities!: (value: Response) => void;
    let resolvePortfolio!: (value: Response) => void;
    const pendingEntities = new Promise<Response>((resolve) => {
      resolveEntities = resolve;
    });
    const pendingPortfolio = new Promise<Response>((resolve) => {
      resolvePortfolio = resolve;
    });
    const rendered = renderAtRoute("/app/entities", (_method, pathname) => {
      const auth = authStub()(_method, pathname);
      if (auth) return auth;
      if (pathname === "/api/v1/entities") return pendingEntities as unknown as Response;
      if (pathname === "/api/v1/portfolio") return pendingPortfolio as unknown as Response;
      return null;
    });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /^entities$/i })).toBeInTheDocument();
    }, WAIT);
    expect(screen.getAllByRole("status", { name: /loading entities/i }).length).toBeGreaterThan(0);
    resolveEntities(jsonResponse(200, []));
    resolvePortfolio(jsonResponse(200, portfolio([])));
    await waitFor(() => {
      expect(screen.getByText("No entities yet")).toBeInTheDocument();
    }, WAIT);
    rendered.unmount();
  });

  it("joins the registry to portfolio state, filters by backend type, and sorts the loaded set", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    const alpha = entity("ent-pay", "Payment Gateway", "ISSUE", ["Payments API"]);
    const beta = entity("ent-checkout", "Checkout", "ISSUE");
    const person = entity("ent-rahul", "Rahul Kumar", "PERSON");
    renderAtRoute("/app/entities", (_method, pathname) => {
      const auth = authStub()(_method, pathname);
      if (auth) return auth;
      if (pathname === "/api/v1/entities") return jsonResponse(200, [alpha, beta, person]);
      if (pathname === "/api/v1/portfolio") {
        return jsonResponse(
          200,
          portfolio([
            portfolioEntity(),
            portfolioEntity({
              entity_id: "ent-checkout",
              canonical_name: "Checkout",
              risk_level: "LOW",
              attention_level: null,
              attention_score: 0,
              impact_count: 0,
              action_count: 0,
              active_insight_count: 0,
              current_state: "IN_PROGRESS",
              observation_count: 5,
            }),
          ]),
        );
      }
      return null;
    });

    await waitFor(() => {
      expect(screen.getByText("Payment Gateway")).toBeInTheDocument();
    }, WAIT);
    const table = screen.getByRole("table", { name: /entities,/i });
    expect(within(table).getByText("Blocked")).toBeInTheDocument();
    expect(within(table).getByText("High · 58")).toBeInTheDocument();
    expect(screen.getByText("3 observations · 1 actions · 1 impacts")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "People" }));
    await waitFor(() => {
      const calls = vi.mocked(globalThis.fetch).mock.calls;
      expect(calls.some(([input]) => String(input).includes("entity_type=PERSON"))).toBe(true);
    }, WAIT);

    await user.click(screen.getByRole("tab", { name: "All" }));
    await user.selectOptions(screen.getByLabelText(/current state/i), "IN_PROGRESS");
    await waitFor(() => {
      expect(screen.getByText("Checkout")).toBeInTheDocument();
    }, WAIT);
    expect(screen.queryByText("Payment Gateway")).not.toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText(/current state/i), "ALL");
    await user.selectOptions(screen.getByLabelText(/^sort$/i), "name");
    await waitFor(() => {
      expect(screen.getByText("Checkout")).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByText("Payment Gateway")).toBeInTheDocument();
  });

  it("searches loaded names and shares the directory state in the URL", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/entities?q=checkout&type=ISSUE&sort=name", (_method, pathname) => {
      const auth = authStub()(_method, pathname);
      if (auth) return auth;
      if (pathname === "/api/v1/entities") {
        return jsonResponse(200, [
          entity("ent-pay", "Payment Gateway"),
          entity("ent-checkout", "Checkout"),
        ]);
      }
      if (pathname === "/api/v1/portfolio") return jsonResponse(200, portfolio([]));
      return null;
    });

    await waitFor(() => {
      expect(screen.getByText("Checkout")).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByRole("searchbox", { name: /search entities/i })).toHaveValue("checkout");
    expect(screen.getByRole("tab", { name: "Issues" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByText("Payment Gateway")).not.toBeInTheDocument();

    await user.clear(screen.getByRole("searchbox", { name: /search entities/i }));
    await user.type(screen.getByRole("searchbox", { name: /search entities/i }), "gateway");
    await waitFor(() => {
      expect(screen.getByText("Payment Gateway")).toBeInTheDocument();
    }, WAIT);
    expect(screen.queryByText("Checkout")).not.toBeInTheDocument();
  });

  it("records an entity and refreshes the tenant directory", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    const recorded = entity("ent-new", "Checkout Recovery");
    let created = false;
    renderAtRoute("/app/entities", (_method, pathname, init) => {
      const auth = authStub("MEMBER")(_method, pathname);
      if (auth) return auth;
      if (pathname === "/api/v1/entities" && init.method === "POST") {
        created = true;
        return jsonResponse(201, recorded);
      }
      if (pathname === "/api/v1/entities") {
        return jsonResponse(200, created ? [recorded] : []);
      }
      if (pathname === "/api/v1/portfolio") return jsonResponse(200, portfolio([]));
      return null;
    });

    await waitFor(() => {
      expect(screen.getByText("No entities yet")).toBeInTheDocument();
    }, WAIT);
    const form = screen.getByRole("form", { name: /record entity/i });
    await user.type(screen.getByLabelText(/name/i), "Checkout Recovery");
    await user.selectOptions(screen.getByLabelText("Type *"), "ISSUE");
    await user.click(screen.getByRole("button", { name: /^record$/i }));
    await waitFor(() => {
      expect(screen.getByText("Checkout Recovery")).toBeInTheDocument();
    }, WAIT);
    expect(form).toBeInTheDocument();
  });

  it("degrades gracefully when the registry or intelligence snapshot fails", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    const failedList = renderAtRoute("/app/entities", (_method, pathname) => {
      const auth = authStub()(_method, pathname);
      if (auth) return auth;
      if (pathname === "/api/v1/entities") return jsonResponse(500, { detail: "boom" });
      if (pathname === "/api/v1/portfolio") return jsonResponse(200, portfolio([]));
      return null;
    });
    await waitFor(() => {
      expect(screen.getByText("Couldn't load entities")).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
    failedList.unmount();

    renderAtRoute("/app/entities", (_method, pathname) => {
      const auth = authStub()(_method, pathname);
      if (auth) return auth;
      if (pathname === "/api/v1/entities") return jsonResponse(200, [entity("ent-pay", "Payment Gateway")]);
      if (pathname === "/api/v1/portfolio") return jsonResponse(500, { detail: "boom" });
      return null;
    });
    await waitFor(() => {
      expect(screen.getByText("Payment Gateway")).toBeInTheDocument();
    }, WAIT);
    expect(
      screen.getByText("Organisation intelligence is temporarily unavailable"),
    ).toBeInTheDocument();
    expect(screen.getByText("No assessed signals")).toBeInTheDocument();
  });

  it("switches same-named entities with the organisation", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    const twoOrgs = mePayload("sam@example.com", [
      membership("org-a", "Alpha", "MEMBER"),
      membership("org-b", "Beta", "MEMBER"),
    ]);
    renderAtRoute("/app/entities", (_method, pathname, init) => {
      if (pathname === "/api/v1/auth/me") return jsonResponse(200, twoOrgs);
      const org = new Headers(init.headers).get("X-Organisation-ID");
      if (pathname === "/api/v1/entities") {
        return jsonResponse(200, [entity("ent-shared", "Payment Gateway")]);
      }
      if (pathname === "/api/v1/portfolio") {
        return jsonResponse(
          200,
          portfolio([
            portfolioEntity({
              entity_id: "ent-shared",
              current_state: org === "org-b" ? "RESOLVED" : "BLOCKED",
              attention_level: org === "org-b" ? null : "HIGH",
              attention_score: org === "org-b" ? 0 : 58,
            }),
          ]),
        );
      }
      return null;
    });

    await waitFor(() => {
      expect(
        within(screen.getByRole("table", { name: /entities,/i })).getByText("Blocked"),
      ).toBeInTheDocument();
    }, WAIT);
    expect(
      within(screen.getByRole("table", { name: /entities,/i })).queryByText("Resolved"),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /switch organisation/i }));
    await user.click(await screen.findByRole("menuitem", { name: /beta/i }));

    await waitFor(() => {
      expect(
        within(screen.getByRole("table", { name: /entities,/i })).getByText("Resolved"),
      ).toBeInTheDocument();
    }, WAIT);
    expect(
      within(screen.getByRole("table", { name: /entities,/i })).queryByText("Blocked"),
    ).not.toBeInTheDocument();
  });
});
