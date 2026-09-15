import { request, type APIRequestContext, type FullConfig } from "@playwright/test";

/* Seeds a dedicated real backend with two distinguishable organisations.
 *
 * The backend under test always starts clean (in-memory stores), so this
 * setup owns bootstrap: one owner with memberships in both organisations,
 * plus a memberless user for the no-organisation recovery path. All names
 * are lowercase because the backend normalises canonical entity names.
 *
 * Nothing here fabricates intelligence: mentions use the same explicit
 * dependency and state vocabulary the backend engines interpret.
 */

const BACKEND = process.env.E2E_BACKEND_URL ?? "http://127.0.0.1:8000";

export const OWNER_EMAIL = "e2e.owner@example.com";
export const PASSWORD = "E2E-Strong-Password-1";
export const NOMEMBER_EMAIL = "e2e.nomember@example.com";

async function api(
  ctx: APIRequestContext,
  method: "GET" | "POST" | "DELETE",
  path: string,
  options: { token?: string; org?: string; body?: unknown } = {},
) {
  const headers: Record<string, string> = {};
  if (options.token) headers.Authorization = `Bearer ${options.token}`;
  if (options.org) headers["X-Organisation-ID"] = options.org;
  const response = await ctx.fetch(`${BACKEND}${path}`, {
    method,
    headers,
    data: options.body,
  });
  if (!response.ok()) {
    throw new Error(`${method} ${path} failed: ${response.status()} ${await response.text()}`);
  }
  return response.json();
}

export default async function globalSetup(_config: FullConfig): Promise<void> {
  const ctx = await request.newContext();
  try {
    const boot = (await api(ctx, "POST", "/api/v1/auth/bootstrap", {
      body: {
        organisation_name: "E2E Alpha Org",
        slug: "e2e-alpha",
        admin_email: OWNER_EMAIL,
        password: PASSWORD,
      },
    })) as { token: { access_token: string }; organisation: { organisation_id: string } };
    const token = boot.token.access_token;
    const orgA = boot.organisation.organisation_id;
    const org = (orgId: string) => ({ token, org: orgId });

    const orgB = (
      (await api(ctx, "POST", "/api/v1/orgs", {
        token,
        body: { name: "E2E Beta Org", slug: "e2e-beta" },
      })) as { organisation_id: string }
    ).organisation_id;

    // Organisation A: a blocked gateway with an explicit dependency.
    await api(ctx, "POST", "/api/v1/meetings", {
      ...org(orgA),
      body: {
        meeting_id: "e2e-alpha-launch",
        title: "E2E Alpha Launch Review",
        transcript:
          "e2e payment gateway is blocked. e2e payment gateway depends on e2e billing helper.",
        meeting_date: "2026-09-10T10:00:00Z",
        participants: ["Sam"],
      },
    });
    await api(ctx, "POST", "/api/v1/meetings", {
      ...org(orgA),
      body: {
        meeting_id: "e2e-alpha-planning",
        title:
          "E2E Alpha Planning Sync for the Extended Cross-Team Checkout Rollout Review Session",
        transcript: "Weekly planning sync with no blocking issues.",
        meeting_date: "2026-09-11T10:00:00Z",
        participants: ["Sam", "Alex"],
      },
    });
    await api(ctx, "POST", "/api/v1/entities", {
      ...org(orgA),
      body: { entity_type: "ISSUE", canonical_name: "e2e payment gateway" },
    });
    await api(ctx, "POST", "/api/v1/entities", {
      ...org(orgA),
      body: { entity_type: "ISSUE", canonical_name: "e2e billing helper" },
    });
    await api(ctx, "POST", "/api/v1/entities/mentions", {
      ...org(orgA),
      body: {
        entity_type: "ISSUE",
        text: "e2e payment gateway",
        meeting_id: "e2e-alpha-launch",
        source_text:
          "e2e payment gateway is blocked. e2e payment gateway depends on e2e billing helper.",
      },
    });

    // Organisation B: a resolved checkout flow, deliberately different.
    await api(ctx, "POST", "/api/v1/meetings", {
      ...org(orgB),
      body: {
        meeting_id: "e2e-beta-checkout",
        title: "E2E Beta Checkout Review",
        transcript: "e2e checkout flow is resolved.",
        meeting_date: "2026-09-12T10:00:00Z",
        participants: ["Sam"],
      },
    });
    await api(ctx, "POST", "/api/v1/entities", {
      ...org(orgB),
      body: { entity_type: "ISSUE", canonical_name: "e2e checkout flow" },
    });
    await api(ctx, "POST", "/api/v1/entities/mentions", {
      ...org(orgB),
      body: {
        entity_type: "ISSUE",
        text: "e2e checkout flow",
        meeting_id: "e2e-beta-checkout",
        source_text: "e2e checkout flow is resolved.",
      },
    });

    // A user with no organisation, for the safe recovery path.
    const added = (await api(ctx, "POST", `/api/v1/orgs/${orgB}/members`, {
      token,
      body: { email: NOMEMBER_EMAIL, role: "MEMBER", password: PASSWORD },
    })) as { user_id: string };
    await api(ctx, "DELETE", `/api/v1/orgs/${orgB}/members/${added.user_id}`, { token });
  } finally {
    await ctx.dispose();
  }
}
