import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { installFetchStub, jsonResponse, renderAtRoute } from "./harness";
import { request } from "../api/client";

/* Product shell and session lifecycle tests: nested active navigation,
 * mobile navigation semantics, logout, expired sessions, tenant-safe URL
 * state, guard navigation, forbidden details, and document titles.
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
    user: { user_id: "u-14", email, status: "ACTIVE", created_at: "2026-01-01T00:00:00Z" },
    memberships,
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

function meetingSummary(overrides: Record<string, unknown> = {}) {
  return {
    meeting_id: "m-1",
    title: "Alpha sync",
    meeting_date: "2026-09-10T10:00:00Z",
    participants: ["Sam"],
    ingested_at: "2026-09-10T11:00:00Z",
    source_revision: 1,
    processing_status: "CURRENT",
    extraction_revision: 1,
    extracted_at: "2026-09-10T11:05:00Z",
    issue_count: 0,
    task_count: 0,
    decision_count: 0,
    risk_count: 0,
    mention_count: 0,
    resolved_entity_count: 0,
    ...overrides,
  };
}

function meetingsEnvelope(meetings: Array<Record<string, unknown>>, limit = 50) {
  return { meetings, limit, returned_count: meetings.length, has_more: false };
}

describe("product shell", () => {
  it("keeps Meetings active on nested meeting routes", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/meetings/review", (_method, pathname) => {
      const auth = authStub()(_method, pathname);
      if (auth) return auth;
      if (pathname === "/api/v1/meetings/review") {
        return jsonResponse(200, {
          meeting_id: "review",
          title: "Launch Review",
          transcript: "Launch is ready.",
          meeting_date: "2026-09-12T10:00:00Z",
          participants: ["Sam"],
          ingested_at: "2026-09-12T10:05:00Z",
        });
      }
      return null;
    });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Launch Review" })).toBeInTheDocument();
    }, WAIT);
    const meetings = screen.getByRole("link", { name: "Meetings", current: "page" });
    expect(meetings).toHaveAttribute("href", "/app/meetings");
    expect(screen.getByRole("link", { name: "Entities", current: false })).toBeInTheDocument();
  });

  it("keeps Entities active on nested entity routes", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/entities/ent-pay", (_method, pathname) => {
      const auth = authStub()(_method, pathname);
      if (auth) return auth;
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
    });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Payment migration" })).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByRole("link", { name: "Entities", current: "page" })).toHaveAttribute(
      "href",
      "/app/entities",
    );
  });

  it("opens, navigates, and closes the mobile navigation structure", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/dashboard", authStub());

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /good (morning|afternoon|evening), sam/i }),
      ).toBeInTheDocument();
    }, WAIT);
    const toggle = screen.getByRole("button", { name: /open navigation/i, expanded: false });
    await user.click(toggle);
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /close navigation/i, expanded: true }),
      ).toBeInTheDocument();
    }, WAIT);

    const drawer = screen.getByRole("complementary", { name: "Mobile navigation" });
    await user.click(within(drawer).getByRole("link", { name: "Intelligence" }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /^intelligence$/i })).toBeInTheDocument();
    }, WAIT);
    expect(
      screen.getByRole("button", { name: /open navigation/i, expanded: false }),
    ).toBeInTheDocument();
  });

  it("signs out, clears the session, and lands on login", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/dashboard", (_method, pathname, init) => {
      const auth = authStub()(_method, pathname);
      if (auth) return auth;
      if (pathname === "/api/v1/auth/logout" && init.method === "POST") {
        return jsonResponse(200, { status: "signed-out" });
      }
      if (pathname === "/api/v1/entities" && !new Headers(init.headers).get("Authorization")) {
        return jsonResponse(401, { detail: "required" });
      }
      return null;
    });

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /good (morning|afternoon|evening), sam/i }),
      ).toBeInTheDocument();
    }, WAIT);
    await user.click(screen.getByRole("button", { name: /account/i }));
    await user.click(await screen.findByRole("menuitem", { name: /sign out/i }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /welcome back/i })).toBeInTheDocument();
    }, WAIT);
    expect(sessionStorage.getItem("tl.session.token")).toBeNull();
    expect(
      screen.queryByRole("heading", { name: /good (morning|afternoon|evening), sam/i }),
    ).not.toBeInTheDocument();
  });

  it("expires a mid-session token and clears local state exactly once", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/dashboard", (_method, pathname) => {
      const auth = authStub()(_method, pathname);
      if (auth) return auth;
      if (pathname === "/api/v1/attention") {
        return jsonResponse(200, { entity_count: 0, items: [] });
      }
      return null;
    });

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /good (morning|afternoon|evening), sam/i }),
      ).toBeInTheDocument();
    }, WAIT);
    installFetchStub((_method, pathname, init) => {
      if (pathname === "/api/v1/auth/me") {
        return jsonResponse(200, mePayload("sam@example.com", [membership("org-a", "Alpha", "MEMBER")]));
      }
      if (pathname === "/api/v1/attention") return jsonResponse(401, { detail: "expired" });
      if (pathname === "/api/v1/entities" && !new Headers(init.headers).get("Authorization")) {
        return jsonResponse(401, { detail: "required" });
      }
      return null;
    });

    await expect(request("/api/v1/attention")).rejects.toThrow();
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /welcome back/i })).toBeInTheDocument();
    }, WAIT);
    expect(sessionStorage.getItem("tl.session.token")).toBeNull();
  });

  it("does not carry one organisation’s list filters into another organisation", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    const twoOrgs = mePayload("sam@example.com", [
      membership("org-a", "Alpha", "MEMBER"),
      membership("org-b", "Beta", "MEMBER"),
    ]);
    renderAtRoute("/app/meetings?q=beta&status=FAILED", (_method, pathname, init) => {
      if (pathname === "/api/v1/auth/me") return jsonResponse(200, twoOrgs);
      if (pathname === "/api/v1/meetings") {
        const org = new Headers(init.headers).get("X-Organisation-ID");
        if (org === "org-b") {
          return jsonResponse(200, meetingsEnvelope([meetingSummary({ meeting_id: "beta", title: "Beta sync" })]));
        }
        return jsonResponse(
          200,
          meetingsEnvelope([
            meetingSummary({ meeting_id: "beta", title: "Beta planning", processing_status: "FAILED" }),
            meetingSummary({ meeting_id: "alpha", title: "Alpha sync" }),
          ]),
        );
      }
      return null;
    });

    const search = await screen.findByRole("searchbox", { name: /search loaded meetings/i }, WAIT);
    expect(search).toHaveValue("beta");
    expect(screen.getByLabelText(/processing/i)).toHaveValue("FAILED");
    await user.click(screen.getByRole("button", { name: /switch organisation/i }));
    await user.click(await screen.findByRole("menuitem", { name: /beta/i }));

    await waitFor(() => {
      expect(screen.getByText("Beta sync")).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByRole("searchbox", { name: /search loaded meetings/i })).toHaveValue("");
    expect(screen.getByLabelText(/processing/i)).toHaveValue("ALL");
  });

  it("re-selecting the active organisation mid-flight keeps the pending query alive", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    let resolveMeetings!: (value: Response) => void;
    const pendingMeetings = new Promise<Response>((resolve) => {
      resolveMeetings = resolve;
    });
    renderAtRoute("/app/meetings", (_method, pathname) => {
      const auth = authStub()(_method, pathname);
      if (auth) return auth;
      if (pathname === "/api/v1/meetings") return pendingMeetings as unknown as Response;
      return null;
    });

    // The header is interactive while the list is still loading: re-selecting
    // the active organisation must be a no-op so the in-flight query keeps
    // its observer. A rejection afterwards must reach the normal error UI
    // instead of stranding the page on skeletons.
    await screen.findByRole("button", { name: /switch organisation/i }, WAIT);
    await user.click(screen.getByRole("button", { name: /switch organisation/i }));
    await user.click(await screen.findByRole("menuitem", { name: /alpha/i }));
    resolveMeetings(jsonResponse(500, { detail: "boom" }));
    await waitFor(() => {
      expect(screen.getByText("Couldn't load meetings")).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("keeps guard recovery inside client-side routing", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/dashboard", (_method, pathname) => {
      if (pathname === "/api/v1/auth/me") {
        return jsonResponse(200, mePayload("sam@example.com", []));
      }
      return null;
    });

    await waitFor(() => {
      expect(screen.getByText("No organisation yet")).toBeInTheDocument();
    }, WAIT);
    await user.click(screen.getByRole("link", { name: /go to settings/i }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /^settings$/i })).toBeInTheDocument();
    }, WAIT);
  });

  it("handles a forbidden detail with the same safe unavailable state", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/meetings/forbidden", (_method, pathname) => {
      const auth = authStub()(_method, pathname);
      if (auth) return auth;
      if (pathname === "/api/v1/meetings/forbidden") {
        return jsonResponse(403, { detail: "Forbidden" });
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
  });

  it.each([
    {
      path: "/app/dashboard",
      heading: /good (morning|afternoon|evening), sam/i,
      title: "Dashboard · ThreadLine",
      stub: "dashboard",
    },
    {
      path: "/app/intelligence",
      heading: /^intelligence$/i,
      title: "Intelligence · ThreadLine",
      stub: "workspace",
    },
    {
      path: "/app/ask",
      heading: /ask threadline/i,
      title: "Ask · ThreadLine",
      stub: "workspace",
    },
    {
      path: "/app/actions",
      heading: /^actions$/i,
      title: "Actions · ThreadLine",
      stub: "workspace",
    },
    {
      path: "/app/settings",
      heading: /^settings$/i,
      title: "Settings · ThreadLine",
      stub: "workspace",
    },
    {
      path: "/app/meetings/review",
      heading: "Launch Review",
      title: "Launch Review · ThreadLine",
      stub: "meeting",
    },
    {
      path: "/app/entities/ent-pay",
      heading: "Payment migration",
      title: "Payment migration · ThreadLine",
      stub: "entity",
    },
  ])("sets the document title for %s", async ({ path, heading, title, stub }) => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute(path, (_method, pathname) => {
      const auth = authStub()(_method, pathname);
      if (auth) return auth;
      if (stub === "dashboard" && pathname === "/api/v1/attention") {
        return jsonResponse(200, { entity_count: 0, items: [] });
      }
      if (stub === "meeting" && pathname === "/api/v1/meetings/review") {
        return jsonResponse(200, {
          meeting_id: "review",
          title: "Launch Review",
          transcript: "Launch is ready.",
          meeting_date: "2026-09-12T10:00:00Z",
          participants: ["Sam"],
          ingested_at: "2026-09-12T10:05:00Z",
        });
      }
      if (stub === "entity" && pathname === "/api/v1/entities/ent-pay") {
        return jsonResponse(200, {
          entity_id: "ent-pay",
          entity_type: "ISSUE",
          canonical_name: "Payment migration",
          aliases: [],
          created_at: "2026-09-01T10:00:00Z",
        });
      }
      return null;
    });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
    }, WAIT);
    expect(document.title).toBe(title);
  });
});
