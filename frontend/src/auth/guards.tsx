/* Route guards: authentication, organisation scope, role floor.
 *
 * Guards redirect or explain; they never fetch. Data components behind them
 * still rely on backend enforcement for every request.
 */

import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "./AuthContext";
import { useOrganisation } from "./OrganisationContext";
import { roleAtLeast } from "./permissions";
import type { Role } from "../types/auth";

export function RequireAuth({ children }: { children: ReactNode }): React.JSX.Element {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "loading") return <GuardSplash label="Checking your session…" />;
  if (status === "bootstrap") {
    return <Navigate to="/setup" replace state={{ from: location.pathname }} />;
  }
  if (status !== "authenticated") {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}

export function RequireOrganisation({ children }: { children: ReactNode }): React.JSX.Element {
  const { organisationId, organisations } = useOrganisation();
  const location = useLocation();

  if (organisationId) return <>{children}</>;
  if (organisations.length === 0) {
    return (
      <GuardMessage
        title="No organisation yet"
        body="Your account isn't a member of any organisation. Ask an owner to invite you, or create one."
        action={{ to: "/app/settings", label: "Go to settings" }}
      />
    );
  }
  return <Navigate to="/app/select-organisation" replace state={{ from: location.pathname }} />;
}

export function RequireRole({
  minimum,
  children,
}: {
  minimum: Role;
  children: ReactNode;
}): React.JSX.Element {
  const { role } = useOrganisation();
  if (!roleAtLeast(role, minimum)) {
    return (
      <GuardMessage
        title="Not permitted"
        body={`This area needs the ${minimum} role. Your current role doesn't include it.`}
      />
    );
  }
  return <>{children}</>;
}

function GuardSplash({ label }: { label: string }): React.JSX.Element {
  return (
    <div className="tl-guard-splash" role="status" aria-live="polite">
      <span className="tl-spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

function GuardMessage({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: { to: string; label: string };
}): React.JSX.Element {
  return (
    <div className="tl-guard-message" role="alert">
      <h1 className="tl-title">{title}</h1>
      <p className="tl-body-secondary">{body}</p>
      {action ? (
        <a className="tl-btn tl-btn-primary" href={action.to}>
          {action.label}
        </a>
      ) : null}
    </div>
  );
}
