import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { jsonResponse, renderAtRoute } from "../../test/harness";

/* Ask integration tests: the real backend answer endpoint, exact citations,
 * tenant resets, failure handling, and navigation to linked workspaces.
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
    user: { user_id: "u-12", email, status: "ACTIVE", created_at: "2026-01-01T00:00:00Z" },
    memberships,
  };
}

function evidenceItem(overrides: Record<string, unknown> = {}) {
  return {
    evidence_id: "ev-pay",
    evidence_type: "ISSUE",
    entity_id: "ent-pay",
    meeting_id: "mtg-1",
    mention_id: "mention-1",
    source_text: "Payment migration is blocked by the billing gateway.",
    timestamp: "2026-09-12T10:00:00Z",
    summary: "Payment migration entered BLOCKED state.",
    severity_weight: 8,
    metadata: {},
    source_reference: "meeting:mtg-1",
    ...overrides,
  };
}

function answerResponse(overrides: Record<string, unknown> = {}) {
  return {
    query_id: "q-1",
    question: "What is blocking payment migration?",
    intent: "ENTITY_STATUS",
    entity_id: "ent-pay",
    answer: "Payment migration is blocked by the billing gateway.",
    evidence: [evidenceItem()],
    cited_evidence_ids: ["ev-pay"],
    insufficient_evidence: false,
    warnings: ["Evidence was truncated due to length limits."],
    generated_at: EVALUATED,
    ...overrides,
  };
}

function askStub(answer: Record<string, unknown> = answerResponse()) {
  return (_method: string, pathname: string) => {
    if (pathname === "/api/v1/auth/me") {
      return jsonResponse(200, mePayload("sam@example.com", [membership("org-a", "Alpha", "MEMBER")]));
    }
    if (pathname === "/api/v1/query" && _method === "POST") return jsonResponse(200, answer);
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
        meeting_date: "2026-09-12T10:00:00Z",
        participants: ["Sam"],
        ingested_at: "2026-09-12T10:05:00Z",
      });
    }
    return null;
  };
}

async function submitQuestion(question: string): Promise<void> {
  await screen.findByRole("heading", { name: /ask threadline/i }, WAIT);
  const user = userEvent.setup();
  await user.type(screen.getByLabelText(/your question/i), question);
  await user.click(screen.getByRole("button", { name: /ask threadline/i }));
}

describe("ask workspace", () => {
  it("renders a generated answer separately from its exact cited evidence", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/ask", askStub());

    await submitQuestion("What is blocking payment migration?");
    await waitFor(() => {
      const answer = within(
        screen.getByRole("heading", { name: /^answer$/i }).closest("section") as HTMLElement,
      );
      expect(
        answer.getByText("Payment migration is blocked by the billing gateway."),
      ).toBeInTheDocument();
    }, WAIT);
    expect(screen.getByText("Question: What is blocking payment migration?")).toBeInTheDocument();
    expect(screen.getByText("Evidence was truncated due to length limits.")).toBeInTheDocument();
    expect(screen.getByText("Payment migration entered BLOCKED state.")).toBeInTheDocument();
    expect(
      screen.getByText("Payment migration is blocked by the billing gateway.", { selector: "blockquote" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/source reference: meeting:mtg-1/i)).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /open linked entity ent-pay/i })[0]).toHaveAttribute(
      "href",
      "/app/entities/ent-pay",
    );
    expect(screen.getAllByRole("link", { name: /open source meeting mtg-1/i })[0]).toHaveAttribute(
      "href",
      "/app/meetings/mtg-1",
    );
  });

  it("reports an ungrounded answer without presenting it as fact", async () => {
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute(
      "/app/ask",
      (_method, pathname) => {
        if (pathname === "/api/v1/auth/me") {
          return jsonResponse(200, mePayload("sam@example.com", [membership("org-a", "Alpha", "MEMBER")]));
        }
        if (pathname === "/api/v1/query") {
          return jsonResponse(
            200,
            answerResponse({
              answer: "ThreadLine does not have sufficient evidence to answer this question.",
              evidence: [],
              cited_evidence_ids: [],
              insufficient_evidence: true,
              warnings: [],
            }),
          );
        }
        return null;
      },
    );

    await submitQuestion("What will happen next quarter?");
    await waitFor(() => {
      expect(screen.getByText("No grounded answer")).toBeInTheDocument();
    }, WAIT);
    expect(
      screen.getByText("ThreadLine does not have sufficient evidence to answer this question."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /^answer$/i })).not.toBeInTheDocument();
    expect(screen.queryByText("Cited evidence")).not.toBeInTheDocument();
  });

  it("retries a failed answer without losing the submitted question", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/ask", (_method, pathname) => {
      if (pathname === "/api/v1/auth/me") {
        return jsonResponse(200, mePayload("sam@example.com", [membership("org-a", "Alpha", "MEMBER")]));
      }
      if (pathname === "/api/v1/query") return jsonResponse(500, { detail: "boom" });
      return null;
    });

    await submitQuestion("What is blocking payment migration?");
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
    }, WAIT);
    const before = vi
      .mocked(globalThis.fetch)
      .mock.calls.filter(([input]) => String(input).endsWith("/api/v1/query")).length;
    await user.click(screen.getByRole("button", { name: /try again/i }));
    await waitFor(() => {
      const after = vi
        .mocked(globalThis.fetch)
        .mock.calls.filter(([input]) => String(input).endsWith("/api/v1/query")).length;
      expect(after).toBeGreaterThan(before);
    }, WAIT);
    expect(screen.getByLabelText(/your question/i)).toHaveValue("What is blocking payment migration?");
  });

  it("clears the previous tenant’s answer when the organisation changes", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    const twoOrgs = mePayload("sam@example.com", [
      membership("org-a", "Alpha", "MEMBER"),
      membership("org-b", "Beta", "MEMBER"),
    ]);
    renderAtRoute("/app/ask", (_method, pathname) => {
      if (pathname === "/api/v1/auth/me") return jsonResponse(200, twoOrgs);
      if (pathname === "/api/v1/query") return jsonResponse(200, answerResponse());
      return null;
    });

    await submitQuestion("What is blocking payment migration?");
    await waitFor(() => {
      const answer = within(
        screen.getByRole("heading", { name: /^answer$/i }).closest("section") as HTMLElement,
      );
      expect(
        answer.getByText("Payment migration is blocked by the billing gateway."),
      ).toBeInTheDocument();
    }, WAIT);
    await user.click(screen.getByRole("button", { name: /switch organisation/i }));
    await user.click(await screen.findByRole("menuitem", { name: /beta/i }));

    await waitFor(() => {
      expect(screen.getByLabelText(/your question/i)).toHaveValue("");
    }, WAIT);
    expect(
      screen.queryAllByText("Payment migration is blocked by the billing gateway."),
    ).toHaveLength(0);
    expect(screen.queryByText("Cited evidence")).not.toBeInTheDocument();
  });

  it("navigates from cited evidence to the entity workspace", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/ask", askStub());

    await submitQuestion("What is blocking payment migration?");
    await waitFor(() => {
      const answer = within(
        screen.getByRole("heading", { name: /^answer$/i }).closest("section") as HTMLElement,
      );
      expect(
        answer.getByText("Payment migration is blocked by the billing gateway."),
      ).toBeInTheDocument();
    }, WAIT);
    await user.click(screen.getAllByRole("link", { name: /open linked entity ent-pay/i })[0]);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Payment migration" })).toBeInTheDocument();
    }, WAIT);
  });

  it("navigates from cited evidence to the meeting workspace", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("tl.session.token", "good-token");
    renderAtRoute("/app/ask", askStub());

    await submitQuestion("What is blocking payment migration?");
    await waitFor(() => {
      const answer = within(
        screen.getByRole("heading", { name: /^answer$/i }).closest("section") as HTMLElement,
      );
      expect(
        answer.getByText("Payment migration is blocked by the billing gateway."),
      ).toBeInTheDocument();
    }, WAIT);
    await user.click(screen.getAllByRole("link", { name: /open source meeting mtg-1/i })[0]);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Payment sync" })).toBeInTheDocument();
    }, WAIT);
  });
});
