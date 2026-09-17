import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { jsonResponse, renderAtRoute } from "../../test/harness";

/* Intelligence workspace integration tests: backend-ordered attention,
 * filtered change stream, repeated signals, movement, follow-up paths,
 * independent failures, tenant scope, shareable filters, and navigation.
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
    user: { user_id: "u-11", email, status: "ACTIVE", created_at: "2026-01-01T00:00:00Z" },
    memberships,
  };
}

const ATTENTION = {
  entity_count: 2,
  items: [
    {
      attention_id: "att-pay",
      entity_id: "ent-pay",
      attention_level: "CRITICAL",
      score: 120,
      reasons: ["ENTITY_BLOCKED"],
      related_insight_ids: ["ins-pay"],
      evaluated_at: "2026-09-13T09:00:00Z",
    },
    {
      attention_id: "att-api",
      entity_id: "ent-api",
      attention_level: "HIGH",
      score: 62,
      reasons: ["RECENT_STATE_CHANGE"],
      related_insight_ids: ["ins-api"],
      evaluated_at: "2026-09-12T10:00:00Z",
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
      attention_score: 62,
      impact_count: 1,
      action_count: 0,
      active_insight_count: 2,
      current_state: "IN_PROGRESS",
      observation_count: 4,
    },
    {
      entity_id: "ent-rahul",
      entity_type: "PERSON",
      canonical_name: "Rahul Kumar",
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
  evaluated_at: EVALUATED,
};

function changeRecord(overrides: Record<string, unknown> = {}) {
  return {
    change_id: "chg-risk",
    entity_id: "ent-pay",
    change_type: "RISK_ESCALATED",
    severity: "CRITICAL",
    detected_at: "2026-09-13T08:00:00Z",
    meeting_id: "mtg-1",
    mention_id: null,
    source_text: "Payment migration is blocked by the billing gateway.",
    previous_state: null,
    current_state: "BLOCKED",
    insight_id: "ins-pay",
    dependency_path: null,
    impact_count: null,
    related_entity_ids: ["ent-api"],
    evidence: "Payment migration entered BLOCKED state with CRITICAL attention.",
    ...overrides,
  };
}

const ALL_CHANGES = [
  changeRecord(),
  changeRecord({
    change_id: "chg-block",
    change_type: "STATE_BLOCKED",
    severity: "HIGH",
    detected_at: "2026-09-12T11:00:00Z",
    previous_state: "IN_PROGRESS",
    current_state: "BLOCKED",
    related_entity_ids: [],
    evidence: "Payment migration transitioned to BLOCKED.",
  }),
  changeRecord({
    change_id: "chg-new",
    change_type: "NEW_DEPENDENCY",
    severity: "MEDIUM",
    detected_at: "2026-09-11T09:00:00Z",
    dependency_path: ["ent-pay", "ent-api"],
    related_entity_ids: ["ent-api"],
    evidence: "Payment migration now explicitly depends on the provider API.",
  }),
  changeRecord({
    change_id: "chg-impact",
    entity_id: "ent-api",
    change_type: "IMPACT_EXPANDED",
    severity: "MEDIUM",
    detected_at: "2026-09-10T10:00:00Z",
    meeting_id: null,
    source_text: null,
    current_state: "IN_PROGRESS",
    impact_count: 2,
    related_entity_ids: ["ent-pay"],
    evidence: "Provider API risk is now associated with payment migration.",
  }),
  changeRecord({
    change_id: "chg-repeat",
    entity_id: "ent-api",
    change_type: "REPEATED_UNRESOLVED",
    severity: "MEDIUM",
    detected_at: "2026-09-11T08:00:00Z",
    meeting_id: "mtg-2",
    current_state: "IN_PROGRESS",
    related_entity_ids: [],
    evidence: "Provider API flakiness was observed repeatedly without progress.",
  }),
  changeRecord({
    change_id: "chg-rahul",
    entity_id: "ent-rahul",
    change_type: "STATE_STARTED",
    severity: "INFO",
    detected_at: "2026-09-09T10:00:00Z",
    meeting_id: null,
    source_text: null,
    previous_state: "OPEN",
    current_state: "IN_PROGRESS",
    related_entity_ids: [],
    evidence: "Rahul Kumar started the assigned investigation.",
  }),
];

const MEETINGS = {
  meetings: [
    {
      meeting_id: "mtg-1",
      title: "Payment sync",
      meeting_date: "2026-09-13T07:30:00Z",
      participants: ["Sam"],
      ingested_at: "2026-09-13T08:00:00Z",
      source_revision: 1,
      processing_status: "CURRENT",
      extraction_revision: 1,
      extracted_at: "2026-09-13T08:05:00Z",
      issue_count: 1,
      task_count: 0,
      decision_count: 0,
      risk_count: 1,
      mention_count: 2,
      resolved_entity_count: 2,
    },
    {
      meeting_id: "mtg-2",
      title: "Provider review",
      meeting_date: "2026-09-11T07:30:00Z",
      participants: ["Sam"],
      ingested_at: "2026-09-11T08:00:00Z",
      source_revision: 1,
      processing_status: "CURRENT",
      extraction_revision: 1,
      extracted_at: "2026-09-11T08:05:00Z",
      issue_count: 1,
      task_count: 0,
      decision_count: 0,
      risk_count: 0,
      mention_count: 1,
      resolved_entity_count: 1,
    },
  ],
  limit: 100,
  returned_count: 2,
  has_more: false,
};

function changesEnvelope(changes: Array<Record<string, unknown>>) {
  const severities = changes.map((change) => change.severity);
  return {
    total_changes: changes.length,
    critical_changes: severities.filter((severity) => severity === "CRITICAL").length,
    high_changes: severities.filter((severity) => severity === "HIGH").length,
    medium_changes: severities.filter((severity) => severity === "MEDIUM").length,
    info_changes: severities.filter((severity) => severity === "INFO").length,
    changes,
    evaluated_at: EVALUATED,
  };
}

function changesStub(overrides: Record<string, (query: string) => Response | null> = {}) {
  return (_method: string, pathname: string, _init: RequestInit, query = "") => {
    if (pathname === "/api/v1/auth/me") {
      return jsonResponse(200, mePayload("sam@example.com", [membership("org-a", "Alpha", "MEMBER")]));
    }
    if (pathname === "/api/v1/attention") return jsonResponse(200, ATTENTION);
    if (pathname === "/api/v1/portfolio") return jsonResponse(200, PORTFOLIO);
    if (pathname === "/api/v1/meetings") return jsonResponse(200, MEETINGS);
    if (pathname === "/api/v1/intelligence/scan-status") {
      return jsonResponse(200, {
        scanned: true,
        completed_at: "2026-09-13T10:00:00Z",
        watermark: 1,
        signal_count: 0,
        new_signal_count: 0,
        new_signal_ids: [],
        truncated: false,
      });
    }
    if (pathname === "/api/v1/changes") {
      if (overrides[pathname]) return overrides[pathname](query);
      const params = new URLSearchParams(query);
      let selected = ALL_CHANGES;
      const severity = params.get("severity");
      const changeType = params.get("change_type");
      if (severity) selected = selected.filter((change) => change.severity === severity);
      if (changeType) selected = selected.filter((change) => change.change_type === changeType);
      const limit = Number(params.get("limit") ?? "50");
      return jsonResponse(200, changesEnvelope(selected.slice(0, limit)));
    }
    if (pathname === "/api/v1/entities/ent-pay") {
      return jsonResponse(200, {
        entity_id: "ent-pay",
        entity_type: "ISSUE",
        canonical_name: "Payment migration",
        aliases: [],
        created_at: "2026-09-01T10:00:00Z",
      });
    }
    if (pathname === "/api/v1/meetings/mtg-1") {
      return jsonResponse(200, {
        meeting_id: "mtg-1",
        title: "Payment sync",
        transcript: "Payment migration is blocked by the billing gateway.",
        meeting_date: "2026-09-13T07:30:00Z",
        participants: ["Sam"],
        ingested_at: "2026-09-13T08:00:00Z",
      });
    }
    return null;
  };
}

function section(name: string): ReturnType<typeof within> {
  return within(screen.getByRole("heading", { name }).closest("section") as HTMLElement);
}

function paymentIsVisible(): void {
  expect(screen.getAllByText("Payment migration").length).toBeGreaterThan(0);
}

describe("intelligence workspace", () => {
  it("shows structural loading states while intelligence resolves", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    const rendered = renderAtRoute(
      "/app/intelligence",
      (_method, pathname) => {
        if (pathname === "/api/v1/auth/me") {
          return jsonResponse(200, mePayload("sam@example.com", [membership("org-a", "Alpha", "MEMBER")]));
        }
        if (
          pathname === "/api/v1/attention" ||
          pathname === "/api/v1/portfolio" ||
          pathname === "/api/v1/changes" ||
          pathname === "/api/v1/meetings"
        ) {
          return new Promise<Response>(() => {}) as unknown as Response;
        }
        return null;
      },
    );

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /^intelligence$/i })).toBeInTheDocument();
    }, WAIT);
    expect(screen.getAllByRole("status", { name: /loading attention signals/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("status", { name: /loading change stream/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("status", { name: /loading repeated signals/i }).length).toBeGreaterThan(0);
    expect(
      screen.getAllByRole("status", { name: /loading dependency and impact movement/i }).length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByRole("status", { name: /loading recent movement/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("status", { name: /loading follow-up paths/i }).length).toBeGreaterThan(0);
    rendered.unmount();
  });

  it("renders attention, filtered changes, movement, and follow-up paths", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/intelligence", changesStub());

    await waitFor(() => {
      paymentIsVisible();
      expect(screen.getByText(/intelligence evaluated/i)).toBeInTheDocument();

      const attention = section("Current attention");
      expect(attention.getAllByRole("link", { name: "Payment migration" })[0]).toHaveAttribute(
        "href",
        "/app/entities/ent-pay",
      );
      expect(attention.getByText("Blocked")).toBeInTheDocument();
      expect(attention.getAllByText(/score 120/i).length).toBe(2);

      const stream = section("Change stream");
      expect(
        stream.getByText("Payment migration entered BLOCKED state with CRITICAL attention."),
      ).toBeInTheDocument();
      expect(stream.getAllByRole("link", { name: /open payment sync/i })[0]).toHaveAttribute(
        "href",
        "/app/meetings/mtg-1",
      );

      const repeated = section("Repeated signals");
      expect(
        repeated.getByText("Provider API flakiness was observed repeatedly without progress."),
      ).toBeInTheDocument();

      const impact = section("Dependency and impact movement");
      expect(
        impact.getByText("Payment migration now explicitly depends on the provider API."),
      ).toBeInTheDocument();
      expect(
        impact.getByText("Provider API risk is now associated with payment migration."),
      ).toBeInTheDocument();

      const recent = section("Recent movement");
      const rows = within(recent.getByRole("list")).getAllByRole("listitem");
      expect(rows[0]?.textContent).toMatch(/risk escalated/i);

      const followUp = section("Where follow-up exists");
      expect(followUp.getAllByRole("link", { name: "Payment migration" })[0]).toHaveAttribute(
        "href",
        "/app/entities/ent-pay",
      );
      expect(followUp.getByText(/1 recommended action/i)).toBeInTheDocument();
    }, WAIT);
  });

  it("shows calm empty states when no intelligence is available", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    const emptyPortfolio = { ...PORTFOLIO, total_entities: 0, entities: [] };
    renderAtRoute(
      "/app/intelligence",
      (_method, pathname) => {
        if (pathname === "/api/v1/auth/me") {
          return jsonResponse(200, mePayload("sam@example.com", [membership("org-a", "Alpha", "MEMBER")]));
        }
        if (pathname === "/api/v1/attention") {
          return jsonResponse(200, { entity_count: 0, items: [] });
        }
        if (pathname === "/api/v1/portfolio") return jsonResponse(200, emptyPortfolio);
        if (pathname === "/api/v1/changes") return jsonResponse(200, changesEnvelope([]));
        if (pathname === "/api/v1/meetings") {
          return jsonResponse(200, { ...MEETINGS, meetings: [], returned_count: 0 });
        }
        return null;
      },
    );

    await waitFor(() => {
      expect(screen.getByText("No active attention signals")).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByText("No changes match these filters")).toBeInTheDocument();
    expect(screen.getByText("No repeated signals")).toBeInTheDocument();
    expect(screen.getByText("No dependency or impact movement")).toBeInTheDocument();
    expect(screen.getByText("No recent movement")).toBeInTheDocument();
    expect(screen.getByText("No follow-up paths")).toBeInTheDocument();
  });

  it("sends backend-supported severity and change-type filters and narrows entity type locally", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/intelligence", changesStub());

    await waitFor(() => {
      paymentIsVisible();
    }, WAIT);
    const stream = section("Change stream");
    await user.selectOptions(screen.getByLabelText(/entity type/i), "PERSON");
    await waitFor(() => {
      expect(stream.getByText("Rahul Kumar started the assigned investigation.")).toBeInTheDocument();
    }, WAIT);
    expect(stream.queryByText("Payment migration transitioned to BLOCKED.")).not.toBeInTheDocument();
    expect(stream.queryByText("Payment migration entered BLOCKED state with CRITICAL attention.")).not.toBeInTheDocument();
    expect(
      vi.mocked(globalThis.fetch).mock.calls.some(([input]) => String(input).includes("entity_type=PERSON")),
    ).toBe(false);

    await user.selectOptions(screen.getByLabelText(/entity type/i), "ALL");
    await user.selectOptions(screen.getByLabelText(/severity/i), "HIGH");
    await user.selectOptions(screen.getByLabelText(/change type/i), "STATE_BLOCKED");
    await waitFor(() => {
      const calls = vi.mocked(globalThis.fetch).mock.calls;
      expect(
        calls.some(
          ([input]) =>
            String(input).includes("/api/v1/changes?") &&
            String(input).includes("severity=HIGH") &&
            String(input).includes("change_type=STATE_BLOCKED"),
        ),
      ).toBe(true);
    }, WAIT);
    await waitFor(() => {
      expect(stream.getByText("Payment migration transitioned to BLOCKED.")).toBeInTheDocument();
    }, WAIT);
    expect(stream.queryByText("Payment migration entered BLOCKED state with CRITICAL attention.")).not.toBeInTheDocument();
  });

  it("ignores unsupported filter values instead of sending them to the backend", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute(
      "/app/intelligence?severity=BOGUS&change_type=BUSINESS_VALUE&entity_type=PROJECT",
      changesStub(),
    );

    await waitFor(() => {
      paymentIsVisible();
    }, WAIT);
    expect(screen.getByLabelText(/severity/i)).toHaveValue("ALL");
    expect(screen.getByLabelText(/change type/i)).toHaveValue("ALL");
    expect(screen.getByLabelText(/entity type/i)).toHaveValue("ALL");
    const calls = vi.mocked(globalThis.fetch).mock.calls;
    const changeCalls = calls
      .map(([input]) => String(input))
      .filter((input) => input.includes("/api/v1/changes?"));
    expect(changeCalls.length).toBeGreaterThan(0);
    for (const call of changeCalls) {
      expect(call).not.toContain("severity=BOGUS");
      expect(call).not.toContain("change_type=BUSINESS_VALUE");
      expect(call).not.toContain("entity_type=PROJECT");
    }
  });

  it("keeps working sections when movement intelligence fails", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    const base = changesStub();
    renderAtRoute(
      "/app/intelligence",
      (_method, pathname, _init, query = "") => {
        if (pathname === "/api/v1/changes" && query.includes("limit=100")) {
          return jsonResponse(500, { detail: "boom" });
        }
        return base(_method, pathname, _init, query);
      },
    );

    await waitFor(() => {
      const impact = section("Dependency and impact movement");
      const recent = section("Recent movement");
      expect(impact.getByText("Couldn't load dependency and impact movement")).toBeInTheDocument();
      expect(recent.getByText("Couldn't load recent movement")).toBeInTheDocument();
      paymentIsVisible();
      const repeated = section("Repeated signals");
      expect(
        repeated.getByText("Provider API flakiness was observed repeatedly without progress."),
      ).toBeInTheDocument();
    }, WAIT);
  });

  it("keeps attention and movement usable when the filtered stream fails", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    const base = changesStub();
    renderAtRoute(
      "/app/intelligence",
      (_method, pathname, _init, query = "") => {
        if (
          pathname === "/api/v1/changes" &&
          query.includes("limit=50") &&
          !query.includes("change_type=REPEATED_UNRESOLVED")
        ) {
          return jsonResponse(500, { detail: "boom" });
        }
        return base(_method, pathname, _init, query);
      },
    );

    await waitFor(() => {
      expect(screen.getByText("Couldn't load changes")).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
    paymentIsVisible();
    expect(
      section("Repeated signals").getByText(
        "Provider API flakiness was observed repeatedly without progress.",
      ),
    ).toBeInTheDocument();
  });

  it("identifies records by ID when directory lookups fail", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute(
      "/app/intelligence",
      (_method, pathname, _init, query = "") => {
        if (pathname === "/api/v1/portfolio" || pathname === "/api/v1/meetings") {
          return jsonResponse(500, { detail: "boom" });
        }
        return changesStub()(_method, pathname, _init, query);
      },
    );

    await waitFor(() => {
      expect(screen.getByText("Organisation directory is temporarily unavailable")).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByText("Meeting titles are temporarily unavailable")).toBeInTheDocument();
    const attention = section("Current attention");
    expect(attention.getAllByRole("link", { name: "Entity ent-pay" })[0]).toHaveAttribute(
      "href",
      "/app/entities/ent-pay",
    );
    const stream = section("Change stream");
    expect(stream.getAllByRole("link", { name: /open meeting mtg-1/i })[0]).toHaveAttribute(
      "href",
      "/app/meetings/mtg-1",
    );
  });

  it("retries a failed intelligence section", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    let movementCalls = 0;
    renderAtRoute(
      "/app/intelligence",
      (_method, pathname, _init, query = "") => {
        const base = changesStub()(_method, pathname, _init, query);
        if (pathname === "/api/v1/changes" && query.includes("limit=100")) {
          movementCalls += 1;
          if (movementCalls < 3) return jsonResponse(500, { detail: "boom" });
        }
        return base;
      },
    );

    await waitFor(() => {
      expect(screen.getByText("Couldn't load dependency and impact movement")).toBeInTheDocument();
    }, WAIT);
    const movement = section("Dependency and impact movement");
    const before = vi
      .mocked(globalThis.fetch)
      .mock.calls.filter(([input]) => String(input).includes("/api/v1/changes?limit=100")).length;
    await user.click(movement.getByRole("button", { name: /try again/i }));
    await waitFor(() => {
      const after = vi
        .mocked(globalThis.fetch)
        .mock.calls.filter(([input]) => String(input).includes("/api/v1/changes?limit=100")).length;
      expect(after).toBeGreaterThan(before);
      expect(
        movement.getByText("Payment migration now explicitly depends on the provider API."),
      ).toBeInTheDocument();
    }, WAIT);
  });

  it("handles forbidden intelligence without leaking backend errors", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute(
      "/app/intelligence",
      (_method, pathname, _init, query = "") => {
        if (pathname === "/api/v1/auth/me") {
          return jsonResponse(200, mePayload("sam@example.com", [membership("org-a", "Alpha", "MEMBER")]));
        }
        if (pathname === "/api/v1/attention") return jsonResponse(403, { detail: "nope" });
        return changesStub()(_method, pathname, _init, query);
      },
    );

    await waitFor(() => {
      expect(screen.getByText("You don't have permission to do that.")).toBeInTheDocument();
      paymentIsVisible();
    }, WAIT);
  });

  it("switches intelligence with the organisation", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    const twoOrgs = mePayload("sam@example.com", [
      membership("org-a", "Alpha", "MEMBER"),
      membership("org-b", "Beta", "MEMBER"),
    ]);
    const betaAttention = { entity_count: 0, items: [] };
    const betaPortfolio = { ...PORTFOLIO, total_entities: 0, entities: [] };
    const betaChanges = changesEnvelope([]);
    renderAtRoute("/app/intelligence", (_method, pathname, init, query = "") => {
      if (pathname === "/api/v1/auth/me") return jsonResponse(200, twoOrgs);
      const org = new Headers(init.headers).get("X-Organisation-ID");
      if (org === "org-b") {
        if (pathname === "/api/v1/attention") return jsonResponse(200, betaAttention);
        if (pathname === "/api/v1/portfolio") return jsonResponse(200, betaPortfolio);
        if (pathname === "/api/v1/changes") return jsonResponse(200, betaChanges);
        if (pathname === "/api/v1/meetings") {
          return jsonResponse(200, { ...MEETINGS, meetings: [], returned_count: 0 });
        }
        return null;
      }
      return changesStub()(_method, pathname, init, query);
    });

    await waitFor(() => {
      paymentIsVisible();
      const repeated = section("Repeated signals");
      expect(
        repeated.getByText("Provider API flakiness was observed repeatedly without progress."),
      ).toBeInTheDocument();
    }, WAIT);
    await user.click(screen.getByRole("button", { name: /switch organisation/i }));
    await user.click(await screen.findByRole("menuitem", { name: /beta/i }));
    await waitFor(() => {
      expect(screen.getByText("No active attention signals")).toBeInTheDocument();
    }, WAIT);
    expect(screen.queryByText("Payment migration")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Provider API flakiness was observed repeatedly without progress."),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/what is changing across beta/i)).toBeInTheDocument();
  });

  it("navigates from attention to the entity workspace", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/intelligence", changesStub());

    await waitFor(() => {
      paymentIsVisible();
    }, WAIT);
    const attention = section("Current attention");
    await user.click(attention.getAllByRole("link", { name: "Payment migration" })[0]);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Payment migration" })).toBeInTheDocument();
    }, WAIT);
  });

  it("navigates from a change to the meeting workspace", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/intelligence", changesStub());

    await waitFor(() => {
      paymentIsVisible();
    }, WAIT);
    const stream = section("Change stream");
    await user.click(stream.getAllByRole("link", { name: /open payment sync/i })[0]);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Payment sync" })).toBeInTheDocument();
    }, WAIT);
  });

  it("never issues one lookup per signal for entity or meeting names", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/intelligence", changesStub());

    await waitFor(() => {
      paymentIsVisible();
    }, WAIT);
    const calls = vi.mocked(globalThis.fetch).mock.calls.map(([input]) => new URL(String(input)).pathname);
    expect(calls.filter((pathname) => /^\/api\/v1\/meetings\/[^/]+$/.test(pathname))).toEqual([]);
    expect(calls.filter((pathname) => /^\/api\/v1\/entities\/[^/]+$/.test(pathname))).toEqual([]);
    expect(calls.filter((pathname) => pathname === "/api/v1/meetings")).toHaveLength(1);
  });

  it("keeps one h1 and the intended section order", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/intelligence", changesStub());

    await waitFor(() => {
      paymentIsVisible();
    }, WAIT);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    const headings = screen
      .getAllByRole("heading", { level: 2 })
      .map((heading) => heading.textContent);
    expect(headings).toEqual([
      "Current attention",
      "Change stream",
      "Repeated signals",
      "Dependency and impact movement",
      "Recent movement",
      "Proactive scan",
      "Where follow-up exists",
    ]);
  });
});
