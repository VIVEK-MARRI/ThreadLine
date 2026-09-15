import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { jsonResponse, renderAtRoute } from "../../test/harness";

/* Entity detail integration tests: current state, unified history, risks,
 * explicit dependencies and impact, related meetings, changes, memory,
 * actions, failure handling, tenant scope, and graph navigation.
 */

const WAIT = { timeout: 15000 } as const;
const EVALUATED = "2026-09-15T10:00:00Z";

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
    user: { user_id: "u-10", email, status: "ACTIVE", created_at: "2026-01-01T00:00:00Z" },
    memberships,
  };
}

function entityResponse(entityId: string, canonicalName: string, entityType = "ISSUE", aliases: string[] = []) {
  return {
    entity_id: entityId,
    entity_type: entityType,
    canonical_name: canonicalName,
    aliases,
    created_at: "2026-09-01T10:00:00Z",
  };
}

function observation(
  index: number,
  meetingId: string,
  meetingTitle: string,
  meetingDate: string,
  overrides: Record<string, unknown> = {},
) {
  return {
    observation_index: index,
    meeting_id: meetingId,
    meeting_title: meetingTitle,
    meeting_date: meetingDate,
    mention_id: `mention-${meetingId}`,
    evidence_text: `Evidence from ${meetingTitle}.`,
    interpreted_state: "UNKNOWN",
    transition_occurred: false,
    from_state: "UNKNOWN",
    to_state: "UNKNOWN",
    is_valid_transition: true,
    transition_skipped_reason: null,
    ...overrides,
  };
}

function observationEvent(
  entityId: string,
  eventId: string,
  meetingId: string,
  occurredAt: string,
  title: string,
) {
  return {
    event_id: eventId,
    entity_id: entityId,
    event_type: "OBSERVATION",
    occurred_at: occurredAt,
    related_meeting_id: meetingId,
    title,
    description: `Observation in meeting: Evidence from ${meetingId}.`,
    event_metadata: { interpreted_state: "UNKNOWN" },
  };
}

function genericMeetings() {
  return Array.from({ length: 11 }, (_, index) => {
    const day = String(index + 2).padStart(2, "0");
    const meetingId = `m-extra-${String(index + 1).padStart(2, "0")}`;
    return { meetingId, title: `Daily Sync ${index + 1}`, date: `2026-09-${day}T10:00:00Z` };
  });
}

function workspace(tenant: "alpha" | "beta") {
  const label = tenant === "alpha" ? "Alpha" : "Beta";
  const firstMeeting = { meetingId: `review-${tenant}-first`, title: `${label} Sprint Planning`, date: "2026-09-01T10:00:00Z" };
  const latestMeeting = { meetingId: `review-${tenant}-latest`, title: `${label} Weekly Review`, date: "2026-09-13T10:00:00Z" };
  const observations = [
    observation(0, firstMeeting.meetingId, firstMeeting.title, firstMeeting.date, {
      interpreted_state: "OPEN",
      from_state: "UNKNOWN",
      to_state: "OPEN",
      transition_occurred: true,
    }),
    ...genericMeetings().map((meeting, index) =>
      observation(index + 1, meeting.meetingId, meeting.title, meeting.date),
    ),
    observation(12, latestMeeting.meetingId, latestMeeting.title, latestMeeting.date, {
      interpreted_state: "BLOCKED",
      from_state: "IN_PROGRESS",
      to_state: "BLOCKED",
      transition_occurred: true,
    }),
  ];
  const events = [
    observationEvent("ent-pay", `event-${tenant}-first`, firstMeeting.meetingId, firstMeeting.date, "Entity observed"),
    ...genericMeetings().map((meeting, index) =>
      observationEvent("ent-pay", `event-${tenant}-${index + 1}`, meeting.meetingId, meeting.date, `Entity observed ${index + 1}`),
    ),
    {
      event_id: `event-${tenant}-state-change`,
      entity_id: "ent-pay",
      event_type: "STATE_CHANGE",
      occurred_at: latestMeeting.date,
      related_meeting_id: latestMeeting.meetingId,
      title: "State changed to BLOCKED",
      description: `Observation in meeting: ${label} gateway is blocked.`,
      event_metadata: { from_state: "IN_PROGRESS", to_state: "BLOCKED" },
    },
  ];

  return {
    label,
    entity: entityResponse("ent-pay", "Payment Gateway", "ISSUE", ["Payments API"]),
    target: entityResponse("ent-dep", `${label} Billing Gateway`),
    upstream: entityResponse("ent-upstream", `${label} Checkout Platform`),
    observer: entityResponse("ent-observer", `${label} Rahul Kumar`, "PERSON"),
    temporal: {
      entity_id: "ent-pay",
      canonical_name: "Payment Gateway",
      entity_type: "ISSUE",
      current_state: tenant === "alpha" ? "BLOCKED" : "RESOLVED",
      observation_count: observations.length,
      transition_count: 2,
      timeline: observations,
    },
    timeline: {
      entity_id: "ent-pay",
      first_observed_at: firstMeeting.date,
      last_observed_at: latestMeeting.date,
      event_count: events.length,
      events,
    },
    memory: {
      entity_id: "ent-pay",
      canonical_name: "Payment Gateway",
      entity_type: "ISSUE",
      first_observed_at: firstMeeting.date,
      last_observed_at: latestMeeting.date,
      meeting_count: 13,
      observation_count: observations.length,
      current_state: tenant === "alpha" ? "BLOCKED" : "RESOLVED",
      facts: [
        {
          fact_type: "FIRST_OBSERVED",
          value: firstMeeting.date,
          source_meeting_id: firstMeeting.meetingId,
          source_mention_id: `mention-${firstMeeting.meetingId}`,
          observed_at: firstMeeting.date,
          detail: firstMeeting.title,
        },
        {
          fact_type: "LAST_OBSERVED",
          value: latestMeeting.date,
          source_meeting_id: latestMeeting.meetingId,
          source_mention_id: `mention-${latestMeeting.meetingId}`,
          observed_at: latestMeeting.date,
          detail: latestMeeting.title,
        },
        {
          fact_type: "CURRENT_STATE",
          value: tenant === "alpha" ? "BLOCKED" : "RESOLVED",
          source_meeting_id: null,
          source_mention_id: null,
          observed_at: null,
          detail: null,
        },
        {
          fact_type: "STATE_TRANSITION",
          value: "IN_PROGRESS → BLOCKED",
          source_meeting_id: latestMeeting.meetingId,
          source_mention_id: `mention-${latestMeeting.meetingId}`,
          observed_at: latestMeeting.date,
          detail: latestMeeting.title,
        },
      ],
    },
    insights: {
      entity_id: "ent-pay",
      insight_count: 2,
      insights: [
        {
          insight_id: `insight-${tenant}-state`,
          entity_id: "ent-pay",
          insight_type: "STATE_CHANGED",
          title: `${label} state changed`,
          description: `${label} gateway moved from UNKNOWN to OPEN.`,
          severity: "INFO",
          observed_at: firstMeeting.date,
          related_meeting_id: firstMeeting.meetingId,
          evidence: `${label} gateway observation.`,
          deterministic_sort_key: `first-${tenant}`,
        },
        {
          insight_id: `insight-${tenant}-blocked`,
          entity_id: "ent-pay",
          insight_type: "ISSUE_BLOCKED",
          title: `${label} issue blocked`,
          description: `${label} gateway is blocked.`,
          severity: "WARNING",
          observed_at: latestMeeting.date,
          related_meeting_id: latestMeeting.meetingId,
          evidence: `${label} gateway is blocked by billing.`,
          deterministic_sort_key: `second-${tenant}`,
        },
      ],
    },
    attention: {
      entity_id: "ent-pay",
      has_attention: tenant === "alpha",
      attention:
        tenant === "alpha"
          ? {
              attention_id: "attention-alpha",
              entity_id: "ent-pay",
              attention_level: "HIGH",
              score: 58,
              reasons: ["ENTITY_BLOCKED"],
              related_insight_ids: ["insight-alpha-blocked"],
              evaluated_at: latestMeeting.date,
            }
          : null,
    },
    actions: {
      entity_id: "ent-pay",
      action_count: 1,
      actions: [
        {
          action_id: `action-${tenant}-escalate`,
          entity_id: "ent-pay",
          action_type: "ESCALATE",
          priority: "HIGH",
          recommended_action: `${label} escalate the blocked gateway.`,
          reason: `${label} gateway is BLOCKED.`,
          related_insight_ids: [`insight-${tenant}-blocked`],
          related_meeting_id: latestMeeting.meetingId,
          created_from_observation_at: latestMeeting.date,
          deterministic_sort_key: `action-${tenant}`,
        },
      ],
    },
    relationships: {
      entity_id: "ent-pay",
      relationship_count: 3,
      related_entity_ids: ["ent-dep", "ent-observer", "ent-upstream"],
      relationships: [
        {
          relationship_id: `rel-${tenant}-depends`,
          source_entity_id: "ent-pay",
          target_entity_id: "ent-dep",
          relationship_type: "DEPENDS_ON",
          evidence_type: "EXPLICIT_STATEMENT",
          evidence: "Explicitly stated in 1 meeting.",
          related_meeting_ids: [latestMeeting.meetingId],
          source_text: `${label} gateway depends on billing.`,
          mention_id: `mention-${latestMeeting.meetingId}`,
          strength: 1,
          deterministic_sort_key: `depends-${tenant}`,
        },
        {
          relationship_id: `rel-${tenant}-blocked`,
          source_entity_id: "ent-upstream",
          target_entity_id: "ent-pay",
          relationship_type: "BLOCKS",
          evidence_type: "EXPLICIT_STATEMENT",
          evidence: "Explicitly stated in 1 meeting.",
          related_meeting_ids: [latestMeeting.meetingId],
          source_text: `${label} checkout platform blocks payment.`,
          mention_id: `mention-${latestMeeting.meetingId}`,
          strength: 1,
          deterministic_sort_key: `blocks-${tenant}`,
        },
        {
          relationship_id: `rel-${tenant}-observed`,
          source_entity_id: "ent-observer",
          target_entity_id: "ent-pay",
          relationship_type: "CO_OCCURS_WITH",
          evidence_type: "CO_OCCURRENCE",
          evidence: "Entities co-occurred in 2 meeting(s).",
          related_meeting_ids: [firstMeeting.meetingId, latestMeeting.meetingId],
          source_text: null,
          mention_id: null,
          strength: 2,
          deterministic_sort_key: `observed-${tenant}`,
        },
      ],
    },
    graph: {
      root_entity_id: "ent-pay",
      direct_dependencies: [
        {
          path_id: `path-${tenant}-direct`,
          start_entity_id: "ent-pay",
          end_entity_id: "ent-dep",
          depth: 1,
          entity_path: ["ent-pay", "ent-dep"],
          relationship_path: ["DEPENDS_ON"],
          edges: [
            {
              source_entity_id: "ent-pay",
              target_entity_id: "ent-dep",
              relationship_type: "DEPENDS_ON",
              strength: 1,
              related_meeting_ids: [latestMeeting.meetingId],
              source_text: `${label} gateway depends on billing.`,
              mention_id: `mention-${latestMeeting.meetingId}`,
            },
          ],
          is_direct: true,
          is_transitive: false,
        },
      ],
      transitive_dependencies: [],
      all_reachable_entity_ids: ["ent-dep"],
      max_depth_reached: 1,
      contains_cycle: false,
      cycle_entity_ids: [],
    },
    impacts: {
      entity_id: "ent-pay",
      impact_count: 1,
      impacts: [
        {
          impact_id: `impact-${tenant}-upstream`,
          source_entity_id: "ent-upstream",
          impacted_entity_id: "ent-pay",
          impact_level: "HIGH",
          risk_signals: ["BLOCKED_ENTITY"],
          relationship_strength: 2,
          related_meeting_ids: [latestMeeting.meetingId],
          reason: `${label} checkout risk is associated with payment.`,
          generated_from_at: latestMeeting.date,
          deterministic_sort_key: `impact-${tenant}`,
        },
      ],
    },
    changes: {
      total_changes: 1,
      critical_changes: 0,
      high_changes: 1,
      medium_changes: 0,
      info_changes: 0,
      changes: [
        {
          change_id: `change-${tenant}-blocked`,
          entity_id: "ent-pay",
          change_type: "STATE_BLOCKED",
          severity: "HIGH",
          detected_at: latestMeeting.date,
          meeting_id: latestMeeting.meetingId,
          mention_id: `mention-${latestMeeting.meetingId}`,
          source_text: `${label} gateway is blocked.`,
          previous_state: "IN_PROGRESS",
          current_state: tenant === "alpha" ? "BLOCKED" : "RESOLVED",
          insight_id: `insight-${tenant}-blocked`,
          dependency_path: null,
          impact_count: null,
          related_entity_ids: ["ent-dep"],
          evidence: `${label} gateway transitioned to BLOCKED.`,
        },
      ],
      evaluated_at: EVALUATED,
    },
  };
}

function emptySection(entityId: string, section: string) {  switch (section) {
    case "temporal":
      return {
        entity_id: entityId,
        canonical_name: entityId,
        entity_type: "ISSUE",
        current_state: "UNKNOWN",
        observation_count: 0,
        transition_count: 0,
        timeline: [],
      };
    case "timeline":
      return { entity_id: entityId, first_observed_at: null, last_observed_at: null, event_count: 0, events: [] };
    case "memory":
      return {
        entity_id: entityId,
        canonical_name: entityId,
        entity_type: "ISSUE",
        first_observed_at: null,
        last_observed_at: null,
        meeting_count: 0,
        observation_count: 0,
        current_state: "UNKNOWN",
        facts: [
          {
            fact_type: "CURRENT_STATE",
            value: "UNKNOWN",
            source_meeting_id: null,
            source_mention_id: null,
            observed_at: null,
            detail: null,
          },
        ],
      };
    case "insights":
      return { entity_id: entityId, insight_count: 0, insights: [] };
    case "attention":
      return { entity_id: entityId, has_attention: false, attention: null };
    case "actions":
      return { entity_id: entityId, action_count: 0, actions: [] };
    case "relationships":
      return { entity_id: entityId, relationship_count: 0, related_entity_ids: [], relationships: [] };
    case "dependency-graph":
      return {
        root_entity_id: entityId,
        direct_dependencies: [],
        transitive_dependencies: [],
        all_reachable_entity_ids: [],
        max_depth_reached: 0,
        contains_cycle: false,
        cycle_entity_ids: [],
      };
    case "impacts":
      return { entity_id: entityId, impact_count: 0, impacts: [] };
    default:
      return null;
  }
}

function detailStub(options: {
  role?: string;
  tenants?: "one" | "two";
  impactFailure?: boolean;
  meetingDetail?: boolean;
} = {}) {
  const role = options.role ?? "MEMBER";
  const tenants = options.tenants ?? "one";
  const twoOrgs = mePayload("sam@example.com", [
    membership("org-a", "Alpha", role),
    ...(tenants === "two" ? [membership("org-b", "Beta", role)] : []),
  ]);
  return (_method: string, pathname: string, init: RequestInit) => {
    if (pathname === "/api/v1/auth/me") return jsonResponse(200, twoOrgs);
    const org = new Headers(init.headers).get("X-Organisation-ID");
    const tenant = tenants === "two" && org === "org-b" ? "beta" : "alpha";
    const scoped = workspace(tenant);
    const entityMatch = pathname.match(/^\/api\/v1\/entities\/([^/]+)$/);
    if (entityMatch) {
      const entityId = decodeURIComponent(entityMatch[1]);
      if (entityId === "ent-pay") return jsonResponse(200, scoped.entity);
      if (entityId === "ent-dep") return jsonResponse(200, scoped.target);
      if (entityId === "ent-upstream") return jsonResponse(200, scoped.upstream);
      if (entityId === "ent-observer") return jsonResponse(200, scoped.observer);
      return null;
    }
    const sectionMatch = pathname.match(/^\/api\/v1\/entities\/([^/]+)\/([^/]+)$/);
    if (sectionMatch) {
      const entityId = decodeURIComponent(sectionMatch[1]);
      const section = sectionMatch[2];
      if (section === "impacts" && options.impactFailure && entityId === "ent-pay") {
        return jsonResponse(500, { detail: "boom" });
      }
      if (entityId !== "ent-pay") return jsonResponse(200, emptySection(entityId, section));
      if (section === "temporal") return jsonResponse(200, scoped.temporal);
      if (section === "timeline") return jsonResponse(200, scoped.timeline);
      if (section === "memory") return jsonResponse(200, scoped.memory);
      if (section === "insights") return jsonResponse(200, scoped.insights);
      if (section === "attention") return jsonResponse(200, scoped.attention);
      if (section === "actions") return jsonResponse(200, scoped.actions);
      if (section === "relationships") return jsonResponse(200, scoped.relationships);
      if (section === "dependency-graph") return jsonResponse(200, scoped.graph);
      if (section === "impacts") return jsonResponse(200, scoped.impacts);
      return null;
    }
    if (pathname === "/api/v1/changes") return jsonResponse(200, scoped.changes);
    if (options.meetingDetail) {
      const meetingMatch = pathname.match(/^\/api\/v1\/meetings\/([^/]+)(\/.*)?$/);
      if (meetingMatch) {
        const meetingId = decodeURIComponent(meetingMatch[1]);
        const suffix = meetingMatch[2] ?? "";
        if (suffix === "") {
          return jsonResponse(200, {
            meeting_id: meetingId,
            title: "Alpha Weekly Review",
            transcript: "Alpha gateway was discussed.",
            meeting_date: "2026-09-13T10:00:00Z",
            participants: ["Sam"],
            ingested_at: "2026-09-13T10:05:00Z",
          });
        }
        if (suffix === "/extraction") {
          return jsonResponse(200, { meeting_id: meetingId, has_extraction: false, extraction: null });
        }
        if (suffix === "/processing") {
          return jsonResponse(200, {
            meeting_id: meetingId,
            source_revision: 1,
            status: "CURRENT",
            processing_complete: true,
            is_current: true,
            extraction_revision: 1,
            derived_revision: 1,
            semantic_revision: 1,
            stale_mentions: 0,
            worker_enabled: true,
          });
        }
        if (suffix === "/mentions") {
          return jsonResponse(200, {
            meeting_id: meetingId,
            mention_count: 0,
            resolved_mention_count: 0,
            mentions: [],
          });
        }
        return null;
      }
    }
    return null;
  };
}

describe("entity detail", () => {
  it("shows a structural loading state", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    let resolveEntity!: (value: Response) => void;
    const pendingEntity = new Promise<Response>((resolve) => {
      resolveEntity = resolve;
    });
    const rendered = renderAtRoute("/app/entities/ent-pay", (_method, pathname) => {
      if (pathname === "/api/v1/auth/me") {
        return jsonResponse(
          200,
          mePayload("sam@example.com", [membership("org-a", "Alpha", "MEMBER")]),
        );
      }
      if (pathname === "/api/v1/entities/ent-pay") {
        return pendingEntity as unknown as Response;
      }
      return null;
    });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /^entity$/i })).toBeInTheDocument();
    }, WAIT);
    expect(
      screen.getAllByRole("status", { name: /loading timeline/i }).length,
    ).toBeGreaterThan(0);
    resolveEntity(jsonResponse(404, { detail: "Entity 'ent-pay' not found." }));
    await waitFor(() => {
      expect(screen.getByText("This entity isn't available")).toBeInTheDocument();
    }, WAIT);
    rendered.unmount();
  });

  it("renders current state, history, risks, connections, meetings, changes, memory, and actions", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/entities/ent-pay", detailStub());

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Payment Gateway" })).toBeInTheDocument();
    }, WAIT);
    await waitFor(() => {
      const current = within(
        screen.getByRole("heading", { name: /current state/i }).closest("section") as HTMLElement,
      );
      const timeline = within(
        screen.getByRole("heading", { name: /^timeline$/i }).closest("section") as HTMLElement,
      );
      const risks = within(
        screen
          .getByRole("heading", { name: /risk and unresolved signals/i })
          .closest("section") as HTMLElement,
      );
      const dependencies = within(
        screen.getByRole("heading", { name: /^dependencies$/i }).closest("section") as HTMLElement,
      );
      const impact = within(
        screen.getByRole("heading", { name: /potential impact/i }).closest("section") as HTMLElement,
      );
      const meetings = within(
        screen.getByRole("heading", { name: /related meetings/i }).closest("section") as HTMLElement,
      );
      const changes = within(
        screen.getByRole("heading", { name: /^changes$/i }).closest("section") as HTMLElement,
      );
      const memory = within(
        screen
          .getByRole("heading", { name: /memory and evidence/i })
          .closest("section") as HTMLElement,
      );
      const actions = within(
        screen
          .getByRole("heading", { name: /attention and recommended actions/i })
          .closest("section") as HTMLElement,
      );

      expect(current.getByText("Blocked")).toBeInTheDocument();
      expect(current.getByText("High")).toBeInTheDocument();
      expect(current.getByText("58")).toBeInTheDocument();
      expect(current.getByText("Blocked entity")).toBeInTheDocument();
      expect(timeline.getByText("State changed to BLOCKED")).toBeInTheDocument();
      expect(risks.getByText("Alpha issue blocked")).toBeInTheDocument();
      expect(
        dependencies.getAllByRole("link", { name: "Alpha Billing Gateway" })[0],
      ).toHaveAttribute("href", "/app/entities/ent-dep");
      expect(
        impact.getByText("Alpha checkout risk is associated with payment."),
      ).toBeInTheDocument();
      expect(
        meetings.getByRole("link", { name: "Alpha Weekly Review" }),
      ).toHaveAttribute("href", "/app/meetings/review-alpha-latest");
      expect(changes.getByText("Alpha gateway transitioned to BLOCKED.")).toBeInTheDocument();
      expect(memory.getByText("First observed")).toBeInTheDocument();
      expect(memory.getByText("Evidence from Alpha Sprint Planning.")).toBeInTheDocument();
      expect(actions.getByText("Alpha escalate the blocked gateway.")).toBeInTheDocument();
    }, WAIT);
  });

  it("expands the unified timeline without losing source links", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/entities/ent-pay", detailStub());

    await waitFor(() => {
      expect(screen.getByText("State changed to BLOCKED")).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByRole("button", { name: /show all 13 events/i })).toBeInTheDocument();
    expect(screen.queryByText("Entity observed")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /show all 13 events/i }));
    await waitFor(() => {
      expect(screen.getByText("Entity observed")).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByRole("button", { name: /show latest events only/i })).toBeInTheDocument();
  });

  it("keeps every other section usable when one intelligence call fails", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/entities/ent-pay", detailStub({ impactFailure: true }));

    await waitFor(() => {
      const current = within(screen.getByRole("heading", { name: /current state/i }).closest("section") as HTMLElement);
      expect(current.getByText("Blocked")).toBeInTheDocument();
    }, WAIT);
    await waitFor(() => {
      expect(screen.getByText("Couldn't load potential impact")).toBeInTheDocument();
    }, WAIT);
    const memory = within(
      screen.getByRole("heading", { name: /memory and evidence/i }).closest("section") as HTMLElement,
    );
    expect(memory.getByText("First observed")).toBeInTheDocument();
  });

  it("handles missing and forbidden entities without revealing tenant scope", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    const missing = renderAtRoute("/app/entities/missing", (_method, pathname) => {
      if (pathname === "/api/v1/auth/me") {
        return jsonResponse(
          200,
          mePayload("sam@example.com", [membership("org-a", "Alpha", "MEMBER")]),
        );
      }
      if (pathname === "/api/v1/entities/missing") {
        return jsonResponse(404, { detail: "Entity 'missing' not found." });
      }
      return null;
    });
    await waitFor(() => {
      expect(screen.getByText("This entity isn't available")).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByRole("link", { name: /back to entities/i })).toHaveAttribute(
      "href",
      "/app/entities",
    );
    missing.unmount();

    renderAtRoute("/app/entities/forbidden", (_method, pathname) => {
      if (pathname === "/api/v1/auth/me") {
        return jsonResponse(
          200,
          mePayload("sam@example.com", [membership("org-a", "Alpha", "MEMBER")]),
        );
      }
      if (pathname === "/api/v1/entities/forbidden") {
        return jsonResponse(403, { detail: "Forbidden" });
      }
      return null;
    });
    await waitFor(() => {
      expect(screen.getByText("This entity isn't available")).toBeInTheDocument();
    }, WAIT);
  });

  it("switches the whole workspace with the organisation", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/entities/ent-pay", detailStub({ tenants: "two" }));

    await waitFor(() => {
      expect(screen.getAllByText("Alpha Weekly Review").length).toBeGreaterThan(0);
    }, WAIT);
    expect(screen.queryByText("Beta Weekly Review")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /switch organisation/i }));
    await user.click(await screen.findByRole("menuitem", { name: /beta/i }));

    await waitFor(() => {
      expect(screen.getAllByText("Beta Weekly Review").length).toBeGreaterThan(0);
    }, WAIT);
    expect(screen.queryByText("Alpha Weekly Review")).not.toBeInTheDocument();
    expect(screen.getAllByText("Beta issue blocked").length).toBeGreaterThan(0);
    expect(
      screen.getAllByRole("link", { name: "Beta Billing Gateway" }).length,
    ).toBeGreaterThan(0);
    expect(
      screen.getAllByText("Beta gateway transitioned to BLOCKED.").length,
    ).toBeGreaterThan(0);
  });

  it("navigates from the directory into the entity, then to related entities and meetings", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/entities", (_method, pathname, init) => {
      const base = detailStub({ meetingDetail: true })(_method, pathname, init);
      if (base) return base;
      if (pathname === "/api/v1/entities") {
        return jsonResponse(200, [entityResponse("ent-pay", "Payment Gateway", "ISSUE", ["Payments API"])]);
      }
      if (pathname === "/api/v1/portfolio") {
        return jsonResponse(200, {
          total_entities: 1,
          critical_entities: 0,
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
              canonical_name: "Payment Gateway",
              risk_level: "HIGH",
              attention_level: "HIGH",
              attention_score: 58,
              impact_count: 1,
              action_count: 1,
              active_insight_count: 2,
              current_state: "BLOCKED",
              observation_count: 13,
            },
          ],
          evaluated_at: EVALUATED,
        });
      }
      return null;
    });

    await waitFor(() => {
      expect(screen.getByRole("link", { name: "Payment Gateway" })).toBeInTheDocument();
    }, WAIT);
    await user.click(screen.getByRole("link", { name: "Payment Gateway" }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Payment Gateway" })).toBeInTheDocument();
    }, WAIT);

    await user.click(screen.getByRole("link", { name: "Alpha Billing Gateway" }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Alpha Billing Gateway" })).toBeInTheDocument();
    }, WAIT);

    await user.click(
      within(screen.getByRole("navigation", { name: /breadcrumb/i })).getByRole("link", {
        name: "Entities",
      }),
    );
    await user.click(screen.getByRole("link", { name: "Payment Gateway" }));
    const relatedMeetings = within(
      screen.getByRole("heading", { name: /related meetings/i }).closest("section") as HTMLElement,
    );
    await waitFor(() => {
      expect(relatedMeetings.getByRole("link", { name: "Alpha Weekly Review" })).toBeInTheDocument();
    }, WAIT);
    await user.click(relatedMeetings.getByRole("link", { name: "Alpha Weekly Review" }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Alpha Weekly Review" })).toBeInTheDocument();
    }, WAIT);
  });

  it("never loads related meetings through per-meeting APIs", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/entities/ent-pay", detailStub());

    await waitFor(() => {
      const meetings = within(
        screen.getByRole("heading", { name: /related meetings/i }).closest("section") as HTMLElement,
      );
      expect(meetings.getByRole("link", { name: "Alpha Weekly Review" })).toBeInTheDocument();
    }, WAIT);
    const calls = vi.mocked(globalThis.fetch).mock.calls;
    expect(
      calls.some(([input]) => /^\/api\/v1\/meetings\/[^/]+$/.test(new URL(String(input)).pathname)),
    ).toBe(false);
  });
});
