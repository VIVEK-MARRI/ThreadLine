import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useOrganisation } from "../../auth/OrganisationContext";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/feedback/States";
import { Logo } from "../../components/layout/Logo";

/* Organisation picker: shown when the caller belongs to several
 * organisations and hasn't selected one. Lists ONLY their memberships. */

export function SelectOrganisationPage(): React.JSX.Element {
  useDocumentTitle("Choose organisation");
  const { organisations, organisationId, select } = useOrganisation();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/app/dashboard";

  if (organisationId) return <Navigate to={from} replace />;

  const active = organisations.filter((m) => m.status === "ACTIVE");

  return (
    <div className="tl-auth-wrap">
      <div className="tl-auth-card tl-auth-card-wide">
        <div className="tl-auth-brand">
          <Logo />
        </div>
        <h1 className="tl-auth-title">Choose your organisation</h1>
        <p className="tl-body-secondary">
          You belong to more than one organisation. Pick where to continue —
          everything you see stays inside that organisation.
        </p>
        {active.length === 0 ? (
          <EmptyState
            title="No organisations"
            body="Your account isn't a member of any organisation yet. Ask an owner to invite you."
          />
        ) : (
          <ul className="tl-org-pick-list">
            {active.map((membership) => (
              <li key={membership.organisation.organisation_id}>
                <button
                  type="button"
                  className="tl-org-pick"
                  onClick={() => {
                    if (select(membership.organisation.organisation_id)) {
                      navigate(from, { replace: true });
                    }
                  }}
                >
                  <span className="tl-org-pick-name">{membership.organisation.name}</span>
                  <span className="tl-org-pick-meta">
                    <Badge tone="neutral">{membership.organisation.slug}</Badge>
                    <Badge tone="teal">{membership.role}</Badge>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
        <Button variant="ghost" onClick={() => navigate("/app/settings")}>
          Go to settings instead
        </Button>
      </div>
    </div>
  );
}
