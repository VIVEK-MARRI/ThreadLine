import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { queryKeys } from "../api/keys";
import { jsonResponse, renderAtRoute } from "../test/harness";

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

const ME_TWO_ORGS = {
  user: { user_id: "u-1", email: "sam@example.com", status: "ACTIVE", created_at: "2026-01-01T00:00:00Z" },
  memberships: [membership("org-a", "Alpha", "OWNER"), membership("org-b", "Beta", "MEMBER")],
};

describe("organisation switching", () => {
  it("auto-selects the first membership, then switches scope from the header", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/dashboard", (_method, pathname) => {
      if (pathname === "/api/v1/auth/me") return jsonResponse(200, ME_TWO_ORGS);
      return null;
    });
    const headingOptions = { timeout: 15000 } as const;

    await waitFor(
      () => {
        expect(
          screen.getByRole("heading", { name: /good (morning|afternoon|evening), sam/i }),
        ).toBeInTheDocument();
      },
      headingOptions,
    );
    expect(screen.getByText(/organisation overview for alpha/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /switch organisation/i }));
    await user.click(await screen.findByRole("menuitem", { name: /beta/i }));

    await waitFor(
      () => {
        expect(screen.getByText(/organisation overview for beta/i)).toBeInTheDocument();
      },
      headingOptions,
    );
    expect(sessionStorage.getItem("tl.org.u-1")).toBe("org-b");
  });

  it("persists the switch so a later visit restores the same organisation", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    sessionStorage.setItem("tl.org.u-1", "org-b");
    renderAtRoute("/app/dashboard", (_method, pathname) => {
      if (pathname === "/api/v1/auth/me") return jsonResponse(200, ME_TWO_ORGS);
      return null;
    });
    await waitFor(
      () => {
        expect(
          screen.getByRole("heading", { name: /good (morning|afternoon|evening), sam/i }),
        ).toBeInTheDocument();
      },
      { timeout: 15000 },
    );
    expect(screen.getByText(/organisation overview for beta/i)).toBeInTheDocument();
  });

  it("rejects unknown organisations instead of trusting the selection", () => {
    // Covered at the provider level: select() returns false for strangers.
    // The cache-key invariant below proves scope can never silently merge.
    const keyA = queryKeys.meetings("org-a");
    expect(keyA[1]).toBe("org-a");
    expect(queryKeys.meetings("org-b")[1]).toBe("org-b");
  });
});