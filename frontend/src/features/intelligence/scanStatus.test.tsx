import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { jsonResponse, renderAtRoute } from "../../test/harness";

/* Stage 34 E10: proactive scan status panel on the Intelligence page.
 * The panel renders backend-persisted scan state only: scanned vs never
 * scanned, signal counts, and newly detected signal IDs. Failure of the
 * scan-status question degrades independently of other sections.
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

function mePayload() {
  return {
    user: { user_id: "u-11", email: "sam@example.com", status: "ACTIVE", created_at: "2026-01-01T00:00:00Z" },
    memberships: [membership("org-a", "Alpha", "MEMBER")],
  };
}

const EMPTY_PORTFOLIO = {
  total_entities: 0,
  critical_entities: 0,
  high_risk_entities: 0,
  medium_risk_entities: 0,
  low_risk_entities: 0,
  entities_with_active_actions: 0,
  entities_with_impact: 0,
  blocked_entities: 0,
  entities: [],
};

function emptyChanges() {
  return {
    total_changes: 0,
    critical_changes: 0,
    high_changes: 0,
    medium_changes: 0,
    info_changes: 0,
    changes: [],
    evaluated_at: "2026-09-13T10:00:00Z",
  };
}

function stub(scanStatus: unknown, scanFails = false) {
  return (_method: string, pathname: string) => {
    if (pathname === "/api/v1/auth/me") return jsonResponse(200, mePayload());
    if (pathname === "/api/v1/attention") {
      return jsonResponse(200, { entity_count: 0, items: [] });
    }
    if (pathname === "/api/v1/portfolio") return jsonResponse(200, EMPTY_PORTFOLIO);
    if (pathname === "/api/v1/meetings") return jsonResponse(200, { meetings: [], total: 0 });
    if (pathname === "/api/v1/changes") return jsonResponse(200, emptyChanges());
    if (pathname === "/api/v1/intelligence/scan-status") {
      if (scanFails) return jsonResponse(500, { detail: "boom" });
      return jsonResponse(200, scanStatus);
    }
    return null;
  };
}

describe("proactive scan status panel", () => {
  it("reports the latest scan with newly detected signals", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute(
      "/app/intelligence",
      stub({
        scanned: true,
        completed_at: "2026-09-13T10:00:00Z",
        watermark: 4,
        signal_count: 5,
        new_signal_count: 2,
        new_signal_ids: ["chg:abc123", "att:def456"],
        truncated: false,
      }),
    );
    await waitFor(() => expect(screen.getByText("Proactive scan")).toBeInTheDocument(), WAIT);
    await waitFor(
      () => expect(screen.getByText(/5 signals observed, 2 newly detected/)).toBeInTheDocument(),
      WAIT,
    );
    expect(screen.getByText("chg:abc123")).toBeInTheDocument();
    expect(screen.getByText("att:def456")).toBeInTheDocument();
  });

  it("reports no-change scans without inventing newness", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute(
      "/app/intelligence",
      stub({
        scanned: true,
        completed_at: "2026-09-13T10:00:00Z",
        watermark: 4,
        signal_count: 5,
        new_signal_count: 0,
        new_signal_ids: [],
        truncated: false,
      }),
    );
    await waitFor(() => expect(screen.getByText("Proactive scan")).toBeInTheDocument(), WAIT);
    await waitFor(
      () => expect(screen.getByText(/0 newly detected/)).toBeInTheDocument(),
      WAIT,
    );
    expect(
      screen.getByText(/No new signals since the previous scan/),
    ).toBeInTheDocument();
  });

  it("shows the never-scanned state without touching other sections", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/intelligence", stub({ scanned: false }));
    await waitFor(() => expect(screen.getByText("Proactive scan")).toBeInTheDocument(), WAIT);
    await waitFor(
      () => expect(screen.getByText("No proactive scan yet")).toBeInTheDocument(),
      WAIT,
    );
  });

  it("degrades independently when scan-status fails", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/intelligence", stub({ scanned: false }, true));
    await waitFor(
      () => expect(screen.getByText("Couldn't load scan status")).toBeInTheDocument(),
      WAIT,
    );
  });
});
