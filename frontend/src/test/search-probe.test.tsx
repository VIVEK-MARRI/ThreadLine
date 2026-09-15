import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { createMemoryRouter, RouterProvider, useLocation, useSearchParams } from "react-router-dom";
import { render } from "@testing-library/react";

function Probe(): React.JSX.Element {
  const location = useLocation();
  const [params] = useSearchParams();
  return (
    <div>
      <span data-testid="search">{location.search}</span>
      <span data-testid="q">{params.get("q") ?? "EMPTY"}</span>
      <span data-testid="pathname">{location.pathname}</span>
    </div>
  );
}

describe("router search probe", () => {
  it("preserves search from string initialEntries", () => {
    const router = createMemoryRouter([{ path: "/app/meetings", element: <Probe /> }], {
      initialEntries: ["/app/meetings?q=beta&status=FAILED"],
    });
    render(<RouterProvider router={router} />);
    expect(screen.getByTestId("pathname").textContent).toBe("/app/meetings");
    expect(screen.getByTestId("search").textContent).toBe("?q=beta&status=FAILED");
    expect(screen.getByTestId("q").textContent).toBe("beta");
  });
});
