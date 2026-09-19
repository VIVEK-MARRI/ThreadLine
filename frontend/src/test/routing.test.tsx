import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ROUTE_PATHS } from "../app/router";
import { jsonResponse, renderAtRoute } from "./harness";

/* Every route resolves without runtime errors. Unauthenticated visits land
 * on login/setup (guards working); the catalogue itself stays stable. */
describe("routing catalogue", () => {
  it.each(ROUTE_PATHS)("%s resolves", async (path) => {
    const { unmount } = renderAtRoute(path, (_method, pathname) => {
      if (pathname === "/api/v1/entities") return jsonResponse(401, { detail: "required" });
      if (pathname === "/api/v1/auth/me") return jsonResponse(401, { detail: "required" });
      return null;
    });
    await waitFor(() => {
      // "/" renders the landing page with a unique hero headline;
      // all other routes resolve through auth guards to login/setup headings.
      const landing = screen.queryByRole("heading", { name: /your organisation remembers/i });
      const auth = screen.queryByRole("heading", { name: /welcome back|set up threadline/i });
      expect(landing ?? auth).toBeInTheDocument();
    });
    unmount();
  });

  it("unknown paths fall back to the app entry", async () => {
    renderAtRoute("/no/such/route", (_method, pathname) => {
      if (pathname === "/api/v1/entities") return jsonResponse(401, { detail: "required" });
      return null;
    });
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /welcome back/i })).toBeInTheDocument();
    });
  });
});
