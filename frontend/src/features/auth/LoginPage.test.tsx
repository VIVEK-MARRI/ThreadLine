import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { jsonResponse, renderAtRoute } from "../../test/harness";

const TOKEN = "session-token-123";
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

function loginStub(seen: { authorization: string | null }) {
  return (method: string, pathname: string, init: RequestInit) => {
    if (pathname === "/api/v1/entities" && method === "GET") {
      const headers = new Headers(init.headers);
      if (!headers.get("Authorization")) return jsonResponse(401, { detail: "required" });
      return jsonResponse(200, []);
    }
    if (pathname === "/api/v1/auth/login" && method === "POST") {
      return jsonResponse(200, {
        access_token: TOKEN,
        token_type: "bearer",
        expires_at: "2026-01-02T00:00:00Z",
      });
    }
    if (pathname === "/api/v1/auth/me") {
      const headers = new Headers(init.headers);
      seen.authorization = headers.get("Authorization");
      if (headers.get("Authorization") !== `Bearer ${TOKEN}`) {
        return jsonResponse(401, { detail: "expired" });
      }
      return jsonResponse(200, ME);
    }
    return null;
  };
}

describe("login flow", () => {
  it("signs in against the backend and lands on the dashboard", async () => {
    const user = userEvent.setup();
    const seen: { authorization: string | null } = { authorization: null };
    renderAtRoute("/login", loginStub(seen));

    const email = await screen.findByLabelText(/email/i);
    await user.type(email, "alice@example.com");
    await user.type(screen.getByLabelText(/password/i), "correct-password");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /good (morning|afternoon|evening), alice/i }),
      ).toBeInTheDocument();
    });
    expect(seen.authorization).toBe(`Bearer ${TOKEN}`);
    expect(sessionStorage.getItem("tl.session.token")).toBe(TOKEN);
  });

  it("shows the backend message on bad credentials and stays put", async () => {
    const user = userEvent.setup();
    renderAtRoute("/login", (_method, pathname) => {
      if (pathname === "/api/v1/entities") return jsonResponse(401, { detail: "required" });
      if (pathname === "/api/v1/auth/login") {
        return jsonResponse(401, { detail: "invalid email or password" });
      }
      return null;
    });

    await user.type(await screen.findByLabelText(/email/i), "alice@example.com");
    await user.type(screen.getByLabelText(/password/i), "wrong");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getByText("invalid email or password")).toBeInTheDocument();
    });
    expect(
      screen.queryByRole("heading", { name: /good (morning|afternoon|evening)/i }),
    ).not.toBeInTheDocument();
  });
});
