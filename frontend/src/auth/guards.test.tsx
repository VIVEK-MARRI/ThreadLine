import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { jsonResponse, renderAtRoute } from "../test/harness";

const ME = {
  user: { user_id: "u-1", email: "alice@example.com", status: "ACTIVE", created_at: "2026-01-01T00:00:00Z" },
  memberships: [
    {
      organisation: {
        organisation_id: "org-1",
        name: "Acme",
        slug: "acme",
        status: "ACTIVE",
        created_at: "2026-01-01T00:00:00Z",
      },
      role: "OWNER",
      status: "ACTIVE",
    },
  ],
};

describe("route guards", () => {
  it("sends unauthenticated visitors to setup when the server is fresh", async () => {
    renderAtRoute("/app/dashboard", (_method, pathname) => {
      if (pathname === "/api/v1/entities") return jsonResponse(200, []);
      return null;
    });
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /set up threadline/i })).toBeInTheDocument();
    });
  });

  it("sends unauthenticated visitors to login when users exist", async () => {
    renderAtRoute("/app/dashboard", (_method, pathname) => {
      if (pathname === "/api/v1/entities") return jsonResponse(401, { detail: "required" });
      return null;
    });
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /welcome back/i })).toBeInTheDocument();
    });
  });

  it("renders protected content for an authenticated session", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/dashboard", (_method, pathname) => {
      if (pathname === "/api/v1/auth/me") return jsonResponse(200, ME);
      return null;
    });
    await waitFor(
      () => {
        expect(
          screen.getByRole("heading", { name: /good (morning|afternoon|evening), alice/i }),
        ).toBeInTheDocument();
      },
      { timeout: 15000 },
    );
    expect(screen.getByText("Acme")).toBeInTheDocument();
  });

  it("redirects an expired session back to login", async () => {
    sessionStorage.setItem("tl.session.token", "stale-token");
    renderAtRoute("/app/dashboard", (_method, pathname) => {
      if (pathname === "/api/v1/auth/me") return jsonResponse(401, { detail: "expired" });
      if (pathname === "/api/v1/entities") return jsonResponse(401, { detail: "required" });
      return null;
    });
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /welcome back/i })).toBeInTheDocument();
    });
    expect(sessionStorage.getItem("tl.session.token")).toBeNull();
  });
});
