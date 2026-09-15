import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { jsonResponse, renderAtRoute } from "./harness";

/* Cross-workspace journeys: Dashboard, Meetings, Entities, and Intelligence
 * must behave as one connected organisational memory system. Each test starts
 * from real rendered content and asserts the destination route, not only an
 * href attribute.
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
    user: { user_id: "u-15", email, status: "ACTIVE", created_at: "2026-01-01T00:00:00Z" },
    memberships,
  };
}

const ATTENTION = {
  entity_count: 1,
  items: [
    {
      attention_id: "att-pay",
      entity_id: "ent-pay",
      attention_level: "CRITICAL",
      score: 120,
      reasons: ["ENTITY_BLOCKED"],
      related_insight_ids: ["ins-pay"],
      evaluated_at: EVALUATED,
    },
  ],
};

const PORTFOLIO = {
  total_entities: 2,
  critical_entities: 1,
  high_risk_entities: 1,
  medium_risk_entities: 0,
  low_risk_entities: 0,
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
      impact_count: 1,
      action_count: 1,
      active_insight_count: 1,
      current_state: "BLOCKED",
      observation_count: 2,
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
      active_insight_count: 1,
      current_state: "IN_PROGRESS",
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

const CHANGES = {
  total_changes: 1,
  critical_changes: 1,
  high_changes: 0,
  medium_changes: 0,
  info_changes: 0,
  changes: [changeRecord()],
  evaluated_at: EVALUATED,
};

function meetingSummary(overrides: Record<string, unknown> = {}) {
  return {
    meeting_id: "mtg-1",
    title: "Payment sync",
    meeting_date: "2026-09-13T07:30:00Z",
    participants: ["Sam"],
    ingested_at: "2026-09-13T08:00:00Z",
    source_revision: 1,
    processing_status: "CURRENT",
    extraction_revision: 1,
    extracted_at: "2026-09-13T08:05:00Z",
    issue_count: 0,
    task_count: 0,
    decision_count: 0,
    risk_count: 1,
    mention_count: 1,
    resolved_entity_count: 1,
    ...overrides,
  };
}

function journeyStub() {
  return (_method: string, pathname: string) => {
    if (pathname === "/api/v1/auth/me") {
      return jsonResponse(200, mePayload("sam@example.com", [membership("org-a", "Alpha", "MEMBER")]));
    }
    if (pathname === "/api/v1/attention") return jsonResponse(200, ATTENTION);
    if (pathname === "/api/v1/portfolio") return jsonResponse(200, PORTFOLIO);
    if (pathname === "/api/v1/changes") return jsonResponse(200, CHANGES);
    if (pathname === "/api/v1/meetings") {
      return jsonResponse(200, {
        meetings: [meetingSummary()],
        limit: 50,
        returned_count: 1,
        has_more: false,
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
    if (pathname === "/api/v1/meetings/mtg-1/mentions") {
      return jsonResponse(200, {
        meeting_id: "mtg-1",
        mention_count: 1,
        resolved_mention_count: 1,
        mentions: [
          {
            mention_id: "mention-pay",
            meeting_id: "mtg-1",
            entity_type: "ISSUE",
            text: "Payment migration",
            source_text: "Payment migration is blocked by the billing gateway.",
            entity_id: "ent-pay",
            resolution_status: "RESOLVED",
            source_revision: 1,
          },
        ],
      });
    }
    if (pathname === "/api/v1/entities") {
      return jsonResponse(200, [
        {
          entity_id: "ent-pay",
          entity_type: "ISSUE",
          canonical_name: "Payment migration",
          aliases: [],
          created_at: "2026-09-01T10:00:00Z",
        },
        {
          entity_id: "ent-api",
          entity_type: "ISSUE",
          canonical_name: "Provider API flakiness",
          aliases: [],
          created_at: "2026-09-01T10:00:00Z",
        },
      ]);
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
    if (pathname === "/api/v1/entities/ent-api") {
      return jsonResponse(200, {
        entity_id: "ent-api",
        entity_type: "ISSUE",
        canonical_name: "Provider API flakiness",
        aliases: [],
        created_at: "2026-09-01T10:00:00Z",
      });
    }
    if (pathname === "/api/v1/entities/ent-pay/temporal") {
      return jsonResponse(200, {
        entity_id: "ent-pay",
        canonical_name: "Payment migration",
        entity_type: "ISSUE",
        current_state: "BLOCKED",
        observation_count: 1,
        transition_count: 1,
        timeline: [
          {
            observation_index: 0,
            meeting_id: "mtg-1",
            meeting_title: "Payment sync",
            meeting_date: "2026-09-13T07:30:00Z",
            mention_id: "mention-pay",
            evidence_text: "Payment migration is blocked by the billing gateway.",
            interpreted_state: "BLOCKED",
            transition_occurred: true,
            from_state: "IN_PROGRESS",
            to_state: "BLOCKED",
            is_valid_transition: true,
            transition_skipped_reason: null,
          },
        ],
      });
    }
    if (pathname === "/api/v1/entities/ent-pay/relationships") {
      return jsonResponse(200, {
        entity_id: "ent-pay",
        relationship_count: 1,
        related_entity_ids: ["ent-api"],
        relationships: [
          {
            relationship_id: "rel-pay-api",
            source_entity_id: "ent-pay",
            target_entity_id: "ent-api",
            relationship_type: "DEPENDS_ON",
            evidence_type: "EXPLICIT_STATEMENT",
            evidence: "Explicitly stated in 1 meeting.",
            related_meeting_ids: ["mtg-1"],
            source_text: "Payment migration is blocked by the billing gateway.",
            mention_id: "mention-pay",
            strength: 1,
            deterministic_sort_key: "000001_DEPENDS_ON_ent-api_rel-pay-api",
          },
        ],
      });
    }
    return null;
  };
}

function section(name: string | RegExp): ReturnType<typeof within> {
  return within(screen.getByRole("heading", { name }).closest("section") as HTMLElement);
}

describe("cross-workspace journeys", () => {
  it("moves from Dashboard attention into Intelligence", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/dashboard", journeyStub());

    await waitFor(() => {
      expect(
        section("Explore").getByRole("link", { name: /intelligence/i }),
      ).toBeInTheDocument();
    }, WAIT);
    await user.click(section("Explore").getByRole("link", { name: /intelligence/i }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /^intelligence$/i })).toBeInTheDocument();
    }, WAIT);
  });

  it("moves from Dashboard attention into Entity detail", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/dashboard", journeyStub());

    await waitFor(() => {
      expect(
        section("Needs attention").getByRole("link", { name: "Payment migration" }),
      ).toBeInTheDocument();
    }, WAIT);
    await user.click(section("Needs attention").getByRole("link", { name: "Payment migration" }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Payment migration" })).toBeInTheDocument();
    }, WAIT);
  });

  it("moves from a Dashboard change into Meeting detail", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/dashboard", journeyStub());

    await waitFor(() => {
      expect(
        section("Recent changes").getByRole("link", { name: /discussed in payment sync/i }),
      ).toBeInTheDocument();
    }, WAIT);
    await user.click(
      section("Recent changes").getByRole("link", { name: /discussed in payment sync/i }),
    );
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Payment sync" })).toBeInTheDocument();
    }, WAIT);
  });

  it("moves from Intelligence attention into Entity detail", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/intelligence", journeyStub());

    await waitFor(() => {
      expect(
        section("Current attention").getAllByRole("link", { name: "Payment migration" })[0],
      ).toBeInTheDocument();
    }, WAIT);
    await user.click(
      section("Current attention").getAllByRole("link", { name: "Payment migration" })[0],
    );
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Payment migration" })).toBeInTheDocument();
    }, WAIT);
  });

  it("moves from an Intelligence change into Meeting detail", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/intelligence", journeyStub());

    await waitFor(() => {
      expect(
        section("Change stream").getAllByRole("link", { name: /open payment sync/i })[0],
      ).toBeInTheDocument();
    }, WAIT);
    await user.click(
      section("Change stream").getAllByRole("link", { name: /open payment sync/i })[0],
    );
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Payment sync" })).toBeInTheDocument();
    }, WAIT);
  });

  it("moves from Entity detail into Meeting detail", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/entities/ent-pay", journeyStub());

    await waitFor(() => {
      expect(
        section("Related meetings").getByRole("link", { name: "Payment sync" }),
      ).toBeInTheDocument();
    }, WAIT);
    await user.click(section("Related meetings").getByRole("link", { name: "Payment sync" }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Payment sync" })).toBeInTheDocument();
    }, WAIT);
  });

  it("moves from an Entity into a related Entity", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/entities/ent-pay", journeyStub());

    await waitFor(() => {
      expect(
        section("Dependencies").getByRole("link", { name: "Provider API flakiness" }),
      ).toBeInTheDocument();
    }, WAIT);
    await user.click(
      section("Dependencies").getByRole("link", { name: "Provider API flakiness" }),
    );
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Provider API flakiness" })).toBeInTheDocument();
    }, WAIT);
  });

  it("moves from Meeting detail into Entity detail", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/meetings/mtg-1", journeyStub());

    await waitFor(() => {
      expect(
        section("People and entities").getByRole("link", { name: "Payment migration" }),
      ).toBeInTheDocument();
    }, WAIT);
    await user.click(
      section("People and entities").getByRole("link", { name: "Payment migration" }),
    );
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Payment migration" })).toBeInTheDocument();
    }, WAIT);
  });

  it("returns from Meeting detail to the Meetings workspace", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/meetings/mtg-1", journeyStub());

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Payment sync" })).toBeInTheDocument();
    }, WAIT);
    await user.click(
      within(screen.getByRole("navigation", { name: /breadcrumb/i })).getByRole("link", {
        name: "Meetings",
      }),
    );
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /^meetings$/i })).toBeInTheDocument();
    }, WAIT);
  });

  it("returns from Entity detail to the Entities workspace", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/entities/ent-pay", journeyStub());

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Payment migration" })).toBeInTheDocument();
    }, WAIT);
    await user.click(
      within(screen.getByRole("navigation", { name: /breadcrumb/i })).getByRole("link", {
        name: "Entities",
      }),
    );
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /^entities$/i })).toBeInTheDocument();
    }, WAIT);
  });
});
