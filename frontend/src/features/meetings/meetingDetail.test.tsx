import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { jsonResponse, renderAtRoute } from "../../test/harness";

/* Meeting detail integration tests: stored extraction, processing lifecycle,
 * entity/dependency/change connections, failure states, and tenant scope.
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
    user: { user_id: "u-8", email, status: "ACTIVE", created_at: "2026-01-01T00:00:00Z" },
    memberships,
  };
}

const DETAIL = {
  meeting_id: "review",
  title: "Product Review",
  transcript: "The team agreed to delay launch. Payment migration is blocked by the billing gateway.",
  meeting_date: "2026-09-10T10:00:00Z",
  participants: ["Alice", "Bob"],
  ingested_at: "2026-09-10T11:00:00Z",
};

const EXTRACTION = {
  meeting_id: "review",
  extracted_at: "2026-09-10T11:05:00Z",
  issues: [
    {
      description: "Payment migration is blocked.",
      evidence: { source_text: "Payment migration is blocked by the billing gateway." },
    },
  ],
  tasks: [
    {
      description: "Confirm the gateway fix date.",
      owner: "Alice",
      deadline: "Friday",
      evidence: { source_text: "Alice will confirm the gateway fix date by Friday." },
    },
  ],
  decisions: [
    {
      description: "Delay launch until the blocker clears.",
      evidence: { source_text: "The team agreed to delay launch." },
    },
  ],
  risks: [
    {
      description: "Launch slips if the gateway stays blocked.",
      severity: "HIGH",
      evidence: { source_text: "A blocked gateway puts launch at risk." },
    },
  ],
};

function processing(overrides: Record<string, unknown> = {}) {
  return {
    meeting_id: "review",
    source_revision: 1,
    status: "CURRENT",
    processing_complete: true,
    is_current: true,
    extraction_revision: 1,
    derived_revision: 1,
    semantic_revision: 1,
    stale_mentions: 0,
    worker_enabled: true,
    ...overrides,
  };
}

const MENTIONS = {
  meeting_id: "review",
  mention_count: 2,
  resolved_mention_count: 2,
  mentions: [
    {
      mention_id: "mention-pay",
      meeting_id: "review",
      entity_type: "ISSUE",
      text: "Payment migration",
      source_text: "Payment migration is blocked by the billing gateway.",
      entity_id: "ent-pay",
      resolution_status: "RESOLVED",
      source_revision: 1,
    },
    {
      mention_id: "mention-alice",
      meeting_id: "review",
      entity_type: "PERSON",
      text: "Alice",
      source_text: "Alice will confirm the gateway fix date by Friday.",
      entity_id: "ent-alice",
      resolution_status: "RESOLVED",
      source_revision: 1,
    },
  ],
};

function entity(entityId: string, canonical_name: string, entity_type: string) {
  return {
    entity_id: entityId,
    entity_type,
    canonical_name,
    aliases: [],
    created_at: "2026-09-01T00:00:00Z",
  };
}

const DEPENDENCY = {
  relationship_id: "rel-1",
  source_entity_id: "ent-pay",
  target_entity_id: "ent-gateway",
  relationship_type: "DEPENDS_ON",
  evidence_type: "EXPLICIT_STATEMENT",
  evidence: "Explicitly stated in 1 meeting.",
  related_meeting_ids: ["review"],
  source_text: "Payment migration is blocked by the billing gateway.",
  mention_id: "mention-pay",
  strength: 1,
  deterministic_sort_key: "000001_DEPENDS_ON_ent-gateway_rel-1",
};

const CHANGES = {
  total_changes: 1,
  critical_changes: 0,
  high_changes: 1,
  medium_changes: 0,
  info_changes: 0,
  changes: [
    {
      change_id: "change-1",
      entity_id: "ent-pay",
      change_type: "STATE_BLOCKED",
      severity: "HIGH",
      detected_at: "2026-09-10T11:00:00Z",
      meeting_id: "review",
      mention_id: "mention-pay",
      source_text: "Payment migration is blocked by the billing gateway.",
      previous_state: "IN_PROGRESS",
      current_state: "BLOCKED",
      insight_id: "insight-1",
      dependency_path: null,
      impact_count: null,
      related_entity_ids: ["ent-gateway"],
      evidence: "Payment migration transitioned to BLOCKED.",
    },
  ],
  evaluated_at: EVALUATED,
};

function workspaceStub(options: { role?: string; processing?: Record<string, unknown> } = {}) {
  const role = options.role ?? "MEMBER";
  return (_method: string, pathname: string, init: RequestInit) => {
    if (pathname === "/api/v1/auth/me") {
      return jsonResponse(200, mePayload("sam@example.com", [membership("org-a", "Alpha", role)]));
    }
    if (pathname === "/api/v1/meetings/review") return jsonResponse(200, DETAIL);
    if (pathname === "/api/v1/meetings/review/extraction") {
      return jsonResponse(200, { meeting_id: "review", has_extraction: true, extraction: EXTRACTION });
    }
    if (pathname === "/api/v1/meetings/review/processing") {
      return jsonResponse(200, processing(options.processing));
    }
    if (pathname === "/api/v1/meetings/review/mentions") return jsonResponse(200, MENTIONS);
    if (pathname === "/api/v1/entities/ent-pay") {
      return jsonResponse(200, entity("ent-pay", "Payment migration", "ISSUE"));
    }
    if (pathname === "/api/v1/entities/ent-alice") {
      return jsonResponse(200, entity("ent-alice", "Alice Example", "PERSON"));
    }
    if (pathname === "/api/v1/entities/ent-gateway") {
      return jsonResponse(200, entity("ent-gateway", "Billing Gateway", "ISSUE"));
    }
    if (pathname === "/api/v1/entities/ent-pay/dependencies") {
      return jsonResponse(200, [DEPENDENCY]);
    }
    if (pathname === "/api/v1/entities/ent-alice/dependencies") return jsonResponse(200, []);
    if (pathname === "/api/v1/changes") return jsonResponse(200, CHANGES);
    if (pathname === "/api/v1/meetings/review/extract" && init.method === "POST") {
      return jsonResponse(200, EXTRACTION);
    }
    return null;
  };
}

describe("meeting detail", () => {
  it("shows a structural loading state", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    let resolveDetail!: (value: Response) => void;
    const pendingDetail = new Promise<Response>((resolve) => {
      resolveDetail = resolve;
    });
    const rendered = renderAtRoute(
      "/app/meetings/review",
      (_method, pathname) => {
        if (pathname === "/api/v1/auth/me") {
          return jsonResponse(
            200,
            mePayload("sam@example.com", [membership("org-a", "Alpha", "MEMBER")]),
          );
        }
        if (pathname === "/api/v1/meetings/review") {
          return pendingDetail as unknown as Response;
        }
        return null;
      },
    );

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /^meeting$/i })).toBeInTheDocument();
    }, WAIT);
    expect(
      screen.getAllByRole("status", { name: /loading meeting facts/i }).length,
    ).toBeGreaterThan(0);
    // Settle the gated query before unmounting: a never-resolving request
    // would otherwise keep its observer alive across tests in this file.
    resolveDetail(jsonResponse(404, { detail: "Meeting 'review' not found." }));
    await waitFor(() => {
      expect(screen.getByText("This meeting isn't available")).toBeInTheDocument();
    }, WAIT);
    rendered.unmount();
  });

  it("renders stored facts and connected organisational memory", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/meetings/review", workspaceStub());

    await waitFor(() => {
      expect(
        within(screen.getByRole("heading", { name: /processing/i }).closest("section") as HTMLElement).getByText(
          "Complete",
        ),
      ).toBeInTheDocument();
    }, WAIT);
    await waitFor(() => {
      expect(screen.getByText("Delay launch until the blocker clears.")).toBeInTheDocument();
      expect(screen.getByText(/owner: alice/i)).toBeInTheDocument();
      expect(screen.getByText(/deadline: friday/i)).toBeInTheDocument();
      expect(screen.getByText("Payment migration is blocked.")).toBeInTheDocument();
      expect(screen.getByText("HIGH")).toBeInTheDocument();

      expect(screen.getAllByText(/depends on/i).length).toBeGreaterThan(0);
      for (const link of screen.getAllByRole("link", { name: "Payment migration" })) {
        expect(link).toHaveAttribute("href", "/app/entities/ent-pay");
      }
      for (const link of screen.getAllByRole("link", { name: "Billing Gateway" })) {
        expect(link).toHaveAttribute("href", "/app/entities/ent-gateway");
      }
      expect(screen.getByText("Payment migration transitioned to BLOCKED.")).toBeInTheDocument();
      expect(screen.getByText(/read full transcript/i)).toBeInTheDocument();
      expect(screen.getByText("review")).toBeInTheDocument();
    }, WAIT);
  });

  it("shows honest empty sections before extraction and mentions exist", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute(
      "/app/meetings/review",
      (_method, pathname) => {
        const base = workspaceStub()(_method, pathname, { method: "GET" } as RequestInit);
        if (pathname === "/api/v1/meetings/review/extraction") {
          return jsonResponse(200, { meeting_id: "review", has_extraction: false, extraction: null });
        }
        if (pathname === "/api/v1/meetings/review/mentions") {
          return jsonResponse(200, {
            meeting_id: "review",
            mention_count: 0,
            resolved_mention_count: 0,
            mentions: [],
          });
        }
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
        return base;
      },
    );

    await waitFor(() => {
      expect(screen.getByText("No extraction is stored for this meeting yet.")).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByText("No mentions recorded")).toBeInTheDocument();
    expect(screen.getByText("No changes attributed to this meeting")).toBeInTheDocument();
  });

  it("queues reprocessing explicitly and reports the queued lifecycle", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/meetings/review", workspaceStub({ processing: { status: "FAILED" } }));

    await waitFor(() => {
      expect(screen.getByText("Processing failed for the current source")).toBeInTheDocument();
    }, WAIT);
    await user.click(screen.getByRole("button", { name: /refresh extraction/i }));
    await waitFor(() => {
      expect(screen.getByText(/reprocessing started/i)).toBeInTheDocument();
    }, WAIT);
  });

  it("says so when the worker is off and work cannot resolve itself", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute(
      "/app/meetings/review",
      workspaceStub({ processing: { status: "PENDING", worker_enabled: false } }),
    );

    await waitFor(() => {
      expect(screen.getByText(/background worker is off/i)).toBeInTheDocument();
    }, WAIT);
  });

  it("handles missing and failed meetings safely", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    const missing = renderAtRoute("/app/meetings/missing", (_method, pathname) => {
      if (pathname === "/api/v1/auth/me") {
        return jsonResponse(
          200,
          mePayload("sam@example.com", [membership("org-a", "Alpha", "MEMBER")]),
        );
      }
      if (pathname === "/api/v1/meetings/missing") {
        return jsonResponse(404, { detail: "Meeting 'missing' not found." });
      }
      return null;
    });
    await waitFor(() => {
      expect(screen.getByText("This meeting isn't available")).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByRole("link", { name: /back to meetings/i })).toHaveAttribute(
      "href",
      "/app/meetings",
    );
    missing.unmount();

    renderAtRoute("/app/meetings/review", (_method, pathname) => {
      if (pathname === "/api/v1/auth/me") {
        return jsonResponse(
          200,
          mePayload("sam@example.com", [membership("org-a", "Alpha", "MEMBER")]),
        );
      }
      if (pathname === "/api/v1/meetings/review") {
        return jsonResponse(500, { detail: "boom" });
      }
      return null;
    });
    await waitFor(() => {
      expect(screen.getByText("Couldn't load this meeting")).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("does not reuse Tenant A detail for Tenant B", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    const twoOrgs = mePayload("sam@example.com", [
      membership("org-a", "Alpha", "OWNER"),
      membership("org-b", "Beta", "MEMBER"),
    ]);
    const alpha = { ...DETAIL, title: "Alpha Review" };
    const beta = { ...DETAIL, title: "Beta Review" };
    renderAtRoute("/app/meetings/review", (_method, pathname, init) => {
      if (pathname === "/api/v1/auth/me") return jsonResponse(200, twoOrgs);
      const org = new Headers(init.headers).get("X-Organisation-ID");
      if (pathname === "/api/v1/meetings/review") {
        return jsonResponse(200, org === "org-b" ? beta : alpha);
      }
      if (pathname === "/api/v1/meetings/review/extraction") {
        return jsonResponse(200, { meeting_id: "review", has_extraction: false, extraction: null });
      }
      if (pathname === "/api/v1/meetings/review/processing") {
        return jsonResponse(200, processing({ status: "INCOMPLETE" }));
      }
      if (pathname === "/api/v1/meetings/review/mentions") {
        return jsonResponse(200, {
          meeting_id: "review",
          mention_count: 0,
          resolved_mention_count: 0,
          mentions: [],
        });
      }
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
      return null;
    });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Alpha Review" })).toBeInTheDocument();
    }, WAIT);
    await user.click(screen.getByRole("button", { name: /switch organisation/i }));
    await user.click(await screen.findByRole("menuitem", { name: /beta/i }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Beta Review" })).toBeInTheDocument();
    }, WAIT);
    expect(screen.queryByRole("heading", { name: "Alpha Review" })).not.toBeInTheDocument();
  });
});
