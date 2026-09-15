import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { jsonResponse, renderAtRoute } from "../../test/harness";

/* Meetings list integration tests: tenant-scoped server list, transparent
 * loaded-set search/filter/sort, creation lifecycle, and tenant switching.
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
    user: { user_id: "u-7", email, status: "ACTIVE", created_at: "2026-01-01T00:00:00Z" },
    memberships,
  };
}

function summary(overrides: Record<string, unknown> = {}) {
  return {
    meeting_id: "m-1",
    title: "Product Review",
    meeting_date: "2026-09-10T10:00:00Z",
    participants: ["Alice", "Bob"],
    ingested_at: "2026-09-10T11:00:00Z",
    source_revision: 1,
    processing_status: "CURRENT",
    extraction_revision: 1,
    extracted_at: "2026-09-10T11:05:00Z",
    issue_count: 1,
    task_count: 1,
    decision_count: 1,
    risk_count: 0,
    mention_count: 2,
    resolved_entity_count: 1,
    ...overrides,
  };
}

function listEnvelope(meetings: ReturnType<typeof summary>[], limit = 50, has_more = false) {
  return { meetings, limit, returned_count: meetings.length, has_more };
}

function authStub(role = "MEMBER") {
  return (_method: string, pathname: string) => {
    if (pathname === "/api/v1/auth/me") {
      return jsonResponse(200, mePayload("sam@example.com", [membership("org-a", "Alpha", role)]));
    }
    return null;
  };
}

describe("meetings list", () => {
  it("shows a structural loading state", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    let resolveMeetings!: (value: Response) => void;
    const pendingMeetings = new Promise<Response>((resolve) => {
      resolveMeetings = resolve;
    });
    const rendered = renderAtRoute(
      "/app/meetings",
      (_method, pathname) => {
        const auth = authStub()(_method, pathname);
        if (auth) return auth;
        if (pathname === "/api/v1/meetings") return pendingMeetings as unknown as Response;
        return null;
      },
    );

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /^meetings$/i })).toBeInTheDocument();
    }, WAIT);
    expect(
      screen.getAllByRole("status", { name: /loading meetings/i }).length,
    ).toBeGreaterThan(0);
    // Settle the gated query before unmounting: a never-resolving request
    // would otherwise keep its observer alive across tests in this file.
    resolveMeetings(jsonResponse(200, listEnvelope([])));
    await waitFor(() => {
      expect(screen.getByText("Your meeting memory starts here.")).toBeInTheDocument();
    }, WAIT);
    rendered.unmount();
  });

  it("searches, filters, sorts, and resizes the loaded meeting set", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    const newer = summary();
    const older = summary({
      meeting_id: "m-2",
      title: "Beta Planning",
      meeting_date: "2026-09-01T10:00:00Z",
      processing_status: "FAILED",
      extraction_revision: null,
      extracted_at: null,
      participants: [],
      mention_count: 0,
      resolved_entity_count: 0,
    });
    renderAtRoute("/app/meetings", (_method, pathname) => {
      const auth = authStub()(_method, pathname);
      if (auth) return auth;
      if (pathname === "/api/v1/meetings") return jsonResponse(200, listEnvelope([newer, older]));
      return null;
    });

    await waitFor(() => {
      expect(screen.getByText("Product Review")).toBeInTheDocument();
    }, WAIT);
    const table = screen.getByRole("table", { name: /meetings, newest first/i });
    expect(
      within(table).getAllByRole("row").slice(1).map((row) => row.textContent),
    ).toMatchObject([expect.stringContaining("Product Review"), expect.stringContaining("Beta Planning")]);

    await user.selectOptions(screen.getByLabelText(/sort/i), "oldest");
    await waitFor(() => {
      expect(screen.getByRole("table", { name: /meetings, oldest first/i })).toBeInTheDocument();
    }, WAIT);
    expect(
      within(screen.getByRole("table", { name: /meetings, oldest first/i }))
        .getAllByRole("row")
        .slice(1)
        .map((row) => row.textContent),
    ).toMatchObject([expect.stringContaining("Beta Planning"), expect.stringContaining("Product Review")]);

    await user.type(screen.getByRole("searchbox", { name: /search loaded meetings/i }), "beta");
    await waitFor(() => {
      expect(screen.queryByText("Product Review")).not.toBeInTheDocument();
    }, WAIT);
    expect(screen.getByText("Beta Planning")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /clear/i }));
    await waitFor(() => {
      expect(screen.getByText("Product Review")).toBeInTheDocument();
    }, WAIT);
    await user.selectOptions(screen.getByLabelText(/processing/i), "FAILED");
    expect(screen.queryByText("Product Review")).not.toBeInTheDocument();
    expect(screen.getByText("Beta Planning")).toBeInTheDocument();
    expect(
      within(screen.getByRole("table", { name: /meetings, newest first/i })).getByText("Failed"),
    ).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText(/page size/i), "100");
    await waitFor(() => {
      const calls = vi.mocked(globalThis.fetch).mock.calls;
      expect(
        calls.some(([input]) => String(input).includes("/api/v1/meetings?limit=100")),
      ).toBe(true);
    }, WAIT);
  });

  it("honours shareable list URLs for search, filters, sort, and page size", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    const newer = summary();
    const older = summary({
      meeting_id: "m-2",
      title: "Beta Planning",
      meeting_date: "2026-09-01T10:00:00Z",
      processing_status: "FAILED",
      extracted_at: null,
      extraction_revision: null,
      participants: [],
    });
    renderAtRoute(
      "/app/meetings?q=beta&status=FAILED&from=2026-09-01&to=2026-09-02&sort=oldest&limit=100",
      (_method, pathname) => {
        const auth = authStub()(_method, pathname);
        if (auth) return auth;
        if (pathname === "/api/v1/meetings") return jsonResponse(200, listEnvelope([newer, older], 100));
        return null;
      },
    );

    await waitFor(() => {
      expect(screen.getByText("Beta Planning")).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByRole("searchbox", { name: /search loaded meetings/i })).toHaveValue("beta");
    expect(screen.getByLabelText(/processing/i)).toHaveValue("FAILED");
    expect(screen.getByLabelText(/sort/i)).toHaveValue("oldest");
    expect(screen.getByLabelText(/page size/i)).toHaveValue("100");
    expect(screen.queryByText("Product Review")).not.toBeInTheDocument();
  });

  it("creates a meeting and navigates into its processing lifecycle", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/meetings", (_method, pathname, init) => {
      const auth = authStub("MEMBER")(_method, pathname);
      if (auth) return auth;
      if (pathname === "/api/v1/meetings" && init.method === "POST") {
        return jsonResponse(201, { meeting_id: "m-new", status: "ingested" });
      }
      if (pathname === "/api/v1/meetings") {
        return jsonResponse(200, listEnvelope([]));
      }
      if (pathname === "/api/v1/meetings/m-new") {
        return jsonResponse(200, {
          meeting_id: "m-new",
          title: "Launch Review",
          transcript: "Launch is ready.",
          meeting_date: "2026-09-12T10:00:00Z",
          participants: ["Sam"],
          ingested_at: "2026-09-12T10:05:00Z",
        });
      }
      if (pathname === "/api/v1/meetings/m-new/extraction") {
        return jsonResponse(200, { meeting_id: "m-new", has_extraction: false, extraction: null });
      }
      if (pathname === "/api/v1/meetings/m-new/processing") {
        return jsonResponse(200, {
          meeting_id: "m-new",
          source_revision: 1,
          status: "PENDING",
          processing_complete: false,
          is_current: false,
          extraction_revision: null,
          derived_revision: null,
          semantic_revision: null,
          stale_mentions: 0,
          worker_enabled: true,
        });
      }
      if (pathname === "/api/v1/meetings/m-new/mentions") {
        return jsonResponse(200, {
          meeting_id: "m-new",
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
          evaluated_at: "2026-09-12T10:06:00Z",
        });
      }
      return null;
    });

    await waitFor(() => {
      expect(screen.getByText("Your meeting memory starts here.")).toBeInTheDocument();
    }, WAIT);
    await user.click(screen.getByRole("button", { name: /create meeting/i }));
    const createForm = await screen.findByRole("form", { name: /new meeting/i }, WAIT);
    await user.type(within(createForm).getByLabelText(/title/i), "Launch Review");
    await user.type(within(createForm).getByLabelText(/transcript/i), "Launch is ready.");
    await user.click(within(createForm).getByRole("button", { name: /^create meeting$/i }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Launch Review" })).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByText(/processing is running/i)).toBeInTheDocument();
  });

  it("keeps a failed ingestion on the workspace with its error", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/meetings", (_method, pathname, init) => {
      const auth = authStub()(_method, pathname);
      if (auth) return auth;
      if (pathname === "/api/v1/meetings" && init.method === "POST") {
        return jsonResponse(500, { detail: "database unavailable" });
      }
      if (pathname === "/api/v1/meetings") return jsonResponse(200, listEnvelope([]));
      return null;
    });

    await waitFor(() => {
      expect(screen.getByText("Your meeting memory starts here.")).toBeInTheDocument();
    }, WAIT);
    await user.click(screen.getByRole("button", { name: /create meeting/i }));
    const failedForm = await screen.findByRole("form", { name: /new meeting/i }, WAIT);
    await user.type(within(failedForm).getByLabelText(/title/i), "Launch Review");
    await user.type(within(failedForm).getByLabelText(/transcript/i), "Launch is ready.");
    await user.click(within(failedForm).getByRole("button", { name: /^create meeting$/i }));

    await waitFor(() => {
      expect(screen.getByText("Couldn't ingest the meeting")).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByRole("heading", { name: /^meetings$/i })).toBeInTheDocument();
  });

  it("shows an empty state, error state, and forbidden state accurately", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    const empty = renderAtRoute("/app/meetings", (_method, pathname) => {
      const auth = authStub()(_method, pathname);
      if (auth) return auth;
      if (pathname === "/api/v1/meetings") return jsonResponse(200, listEnvelope([]));
      return null;
    });
    await waitFor(() => {
      expect(screen.getByText("Your meeting memory starts here.")).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByRole("button", { name: /create meeting/i })).toBeInTheDocument();
    empty.unmount();

    renderAtRoute("/app/meetings", (_method, pathname) => {
      const auth = authStub()(_method, pathname);
      if (auth) return auth;
      if (pathname === "/api/v1/meetings") return jsonResponse(500, { detail: "boom" });
      return null;
    });
    await waitFor(() => {
      expect(screen.getByText("Couldn't load meetings")).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("switches same-named meetings with the organisation", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    const twoOrgs = mePayload("sam@example.com", [
      membership("org-a", "Alpha", "OWNER"),
      membership("org-b", "Beta", "MEMBER"),
    ]);
    const alpha = summary({ meeting_id: "review", participants: ["Alice Alpha"] });
    const beta = summary({
      meeting_id: "review",
      meeting_date: "2026-09-11T10:00:00Z",
      participants: ["Bob Beta"],
    });
    renderAtRoute("/app/meetings", (_method, pathname, init) => {
      if (pathname === "/api/v1/auth/me") return jsonResponse(200, twoOrgs);
      if (pathname === "/api/v1/meetings") {
        const org = new Headers(init.headers).get("X-Organisation-ID");
        return jsonResponse(200, listEnvelope(org === "org-b" ? [beta] : [alpha]));
      }
      return null;
    });

    await waitFor(() => {
      expect(screen.getByText(/alice alpha/i)).toBeInTheDocument();
    }, WAIT);
    expect(screen.queryByText(/bob beta/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /switch organisation/i }));
    await user.click(await screen.findByRole("menuitem", { name: /beta/i }));

    await waitFor(() => {
      expect(screen.getByText(/bob beta/i)).toBeInTheDocument();
    }, WAIT);
    expect(screen.queryByText(/alice alpha/i)).not.toBeInTheDocument();
  });
});
