import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Badge, StatusBadge } from "./Badge";
import { Button } from "./Button";
import { Field, Input } from "./Input";
import { EmptyState, ErrorState } from "../feedback/States";
import { ApiError } from "../../types/api";

describe("design-system components", () => {
  it("StatusBadge pairs tone with a text label (never color alone)", () => {
    render(<StatusBadge tone="danger" label="Failed" />);
    const badge = screen.getByText("Failed");
    expect(badge.className).toContain("tl-badge-danger");
    expect(badge.querySelector(".tl-status-dot")).not.toBeNull();
  });

  it("Badge renders quiet metadata pills", () => {
    render(<Badge tone="teal">OWNER</Badge>);
    expect(screen.getByText("OWNER").className).toContain("tl-badge-teal");
  });

  it("Button shows a loading state and blocks repeat clicks", async () => {
    const user = userEvent.setup();
    let clicks = 0;
    render(
      <Button loading onClick={() => { clicks += 1; }}>
        Saving
      </Button>,
    );
    const button = screen.getByRole("button", { name: /saving/i });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    await user.click(button);
    expect(clicks).toBe(0);
  });

  it("Field wires labels, hints, and errors accessibly", () => {
    render(
      <Field label="Email" htmlFor="email-x" hint="Work email" error="Required">
        <Input id="email-x" invalid />
      </Field>,
    );
    const input = screen.getByLabelText("Email");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAttribute("aria-describedby", "email-x-hint email-x-error");
    expect(screen.getByRole("alert")).toHaveTextContent("Required");
  });

  it("EmptyState offers the next action", () => {
    render(
      <EmptyState
        title="No meetings yet"
        body="Ingest your first transcript."
        action={<button type="button">Ingest a meeting</button>}
      />,
    );
    expect(screen.getByRole("heading", { name: /no meetings yet/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /ingest a meeting/i })).toBeInTheDocument();
  });

  it("ErrorState translates backend failures into friendly copy with retry", async () => {
    const user = userEvent.setup();
    let retried = false;
    render(
      <ErrorState
        error={new ApiError({ kind: "network", status: null, message: "TypeError: Failed to fetch" })}
        onRetry={() => {
          retried = true;
        }}
      />,
    );
    expect(screen.queryByText(/failed to fetch/i)).not.toBeInTheDocument();
    expect(screen.getByText(/check your connection/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /try again/i }));
    expect(retried).toBe(true);
  });
});
